"""
Multi-Model Fusion + Bias Correction + Nowcasting

Blends 3 independent forecast sources for sharper probability estimates:
  1. Open-Meteo 30-member ensemble (current)
  2. NOAA NWS official point forecast (deterministic)
  3. Open-Meteo GFS+HRRR seamless (high-res nowcast for US)

Each source returns a temperature forecast. We bias-correct per city/month,
compute bucket probabilities independently, then fuse with weighted average.
Confidence score gates trading decisions.
"""

import math
import os
import sqlite3
import requests
from typing import List, Optional, Tuple
from weather.forecast import get_ensemble_max_temps, get_ensemble_min_temps, get_bucket_prob
from weather.forecast import get_ensemble_precip, get_nws_precip_forecast
from weather import cache as fcache
from weather.precip_model import gamma_precip_prob
from config import BIAS_DB_PATH, FUSION_WEIGHTS, PRECIP_FUSION_WEIGHTS


def _get_liquidity_score(market: dict) -> float:
    """Normalize volume + open interest to [0, 1] for confidence scoring.
    Typical Kalshi weather markets: $500 ~ 0.2, $5k ~ 0.6, $50k+ ~ 1.0"""
    volume = float(market.get("volume_24h_fp", 0) or 0)
    oi = float(market.get("open_interest_fp", 0) or 0)
    combined = volume + (oi * 0.6)  # OI slightly downweighted
    return min(1.0, max(0.0, math.log(combined + 100) / math.log(50000)))


def _calculate_confidence(
    agreement: float,
    spread_norm: float,
    bias_available: float,
    csgd_success: float,
    nws_agreement: float,
    horizon_days: int,
    liquidity_score: float = 0.5,
) -> float:
    """Nonlinear continuous confidence (40-100). Power weighting spreads values naturally.

    Power-law exponents on agreement (1.2) and spread (1.1) reward strong signals
    disproportionately — high values get amplified, mediocre values get compressed.
    This eliminates the clustering that the old linear integer model produced and
    gives the sigmoid Kelly real dynamic range.

    Inputs (all 0-1 unless noted):
        agreement:       model agreement (power-weighted 0.35)
        spread_norm:     ensemble tightness (power-weighted 0.30)
        bias_available:  historical bias data depth (0.15)
        csgd_success:    model fit quality, continuous for precip (0.10)
        nws_agreement:   strength of NWS match, not just presence (0.05)
        horizon_days:    days to settlement — closer = more confident (0.03)
        liquidity_score: normalized volume/OI, default 0.5 (0.02)
    """
    score = (
        0.35 * agreement ** 1.2
        + 0.30 * spread_norm ** 1.1
        + 0.15 * bias_available
        + 0.10 * csgd_success
        + 0.05 * nws_agreement
        + 0.03 * (1.0 / max(horizon_days, 1))
        + 0.02 * liquidity_score
    ) * 100
    return max(40.0, min(100.0, score))


# ---------------------------------------------------------------------------
# SQLite bias table
# ---------------------------------------------------------------------------

def _get_db():
    os.makedirs(os.path.dirname(BIAS_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(BIAS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bias (
            city TEXT,
            month INTEGER,
            model TEXT,
            avg_bias REAL,
            sample_count INTEGER,
            last_updated TEXT,
            PRIMARY KEY (city, month, model)
        )
    """)
    conn.commit()
    return conn


def get_bias(city: str, month: int, model: str) -> Tuple[float, int]:
    """Return (avg_bias, sample_count) for a city/month/model. Defaults to (0, 0)."""
    conn = _get_db()
    row = conn.execute(
        "SELECT avg_bias, sample_count FROM bias WHERE city=? AND month=? AND model=?",
        (city, month, model),
    ).fetchone()
    conn.close()
    return (row[0], row[1]) if row else (0.0, 0)


def update_bias(city: str, month: int, model: str, forecast_high: float, actual_high: float):
    """Incrementally update running bias average after a market resolves."""
    conn = _get_db()
    row = conn.execute(
        "SELECT avg_bias, sample_count FROM bias WHERE city=? AND month=? AND model=?",
        (city, month, model),
    ).fetchone()
    if row:
        old_avg, n = row
        new_n = n + 1
        new_avg = old_avg + (forecast_high - actual_high - old_avg) / new_n
    else:
        new_avg = forecast_high - actual_high
        new_n = 1
    conn.execute(
        """INSERT INTO bias (city, month, model, avg_bias, sample_count, last_updated)
           VALUES (?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(city, month, model) DO UPDATE SET
             avg_bias=excluded.avg_bias, sample_count=excluded.sample_count,
             last_updated=excluded.last_updated""",
        (city, month, model, round(new_avg, 2), new_n),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# NOAA NWS Point Forecast
# ---------------------------------------------------------------------------

_nws_cache = {}  # (lat, lon) -> forecast_url

NWS_HEADERS = {"User-Agent": "WeatherEdgeBot/1.0 (ethanede@gmail.com)", "Accept": "application/geo+json"}


def get_noaa_point_forecast(lat: float, lon: float, days_ahead: int = 1, unit: str = "f", temp_type: str = "max") -> Optional[float]:
    """Fetch NOAA NWS official point forecast temperature.
    temp_type: "max" for daytime high, "min" for nighttime low.
    Returns a single deterministic temp, or None on failure."""
    try:
        cache_key = (round(lat, 4), round(lon, 4))
        if cache_key not in _nws_cache:
            points_url = f"https://api.weather.gov/points/{lat},{lon}"
            r = requests.get(points_url, headers=NWS_HEADERS, timeout=10)
            r.raise_for_status()
            forecast_url = r.json()["properties"]["forecast"]
            _nws_cache[cache_key] = forecast_url
        else:
            forecast_url = _nws_cache[cache_key]

        r = requests.get(forecast_url, headers=NWS_HEADERS, timeout=10)
        r.raise_for_status()
        periods = r.json()["properties"]["periods"]

        # For max: find daytime period. For min: find nighttime period.
        want_daytime = (temp_type == "max")
        remaining = days_ahead
        target_idx = 0
        for i, p in enumerate(periods):
            if p.get("isDaytime", False) == want_daytime:
                if remaining == 0:
                    target_idx = i
                    break
                remaining -= 1

        period = periods[target_idx]
        temp = float(period["temperature"])
        temp_unit = period.get("temperatureUnit", "F")

        # Convert if needed
        if unit == "c" and temp_unit == "F":
            temp = round((temp - 32) * 5 / 9, 1)
        elif unit == "f" and temp_unit == "C":
            temp = round(temp * 9 / 5 + 32, 1)

        return temp
    except Exception as e:
        print(f"  NOAA forecast error: {e}")
        return None


# ---------------------------------------------------------------------------
# GFS + HRRR seamless (via Open-Meteo)
# ---------------------------------------------------------------------------

def get_hrrr_forecast(lat: float, lon: float, days_ahead: int = 1, unit: str = "f", temp_type: str = "max") -> Optional[float]:
    """Fetch GFS+HRRR seamless forecast from Open-Meteo.
    HRRR auto-activates for US locations at high resolution.
    temp_type: "max" or "min".
    Returns a single deterministic temp, or None on failure."""
    try:
        unit_param = "fahrenheit" if unit == "f" else "celsius"
        daily_var = "temperature_2m_max" if temp_type == "max" else "temperature_2m_min"
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&daily={daily_var}"
            f"&models=gfs_seamless"
            f"&temperature_unit={unit_param}"
            f"&timezone=auto"
            f"&forecast_days={days_ahead + 2}"
        )
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        temps = data.get("daily", {}).get(daily_var, [])
        if len(temps) > days_ahead and temps[days_ahead] is not None:
            return round(float(temps[days_ahead]), 1)
        return None
    except Exception as e:
        print(f"  HRRR/GFS forecast error: {e}")
        return None


# ---------------------------------------------------------------------------
# Probability from a deterministic forecast
# ---------------------------------------------------------------------------

def _deterministic_bucket_prob(temp: float, low: Optional[float], high: Optional[float], spread: float = 3.0) -> float:
    """Convert a single-point forecast into a bucket probability.

    Uses a simple triangular spread around the point forecast to simulate
    uncertainty. spread controls the half-width in degrees.
    """
    if low is None and high is not None:
        # < high bucket (inverse of >= bucket)
        if temp <= high - spread:
            return 0.95
        elif temp >= high + spread:
            return 0.05
        else:
            return round(0.5 + 0.45 * (high - temp) / spread, 4)
    elif high is None:
        # >= low bucket
        if temp >= low + spread:
            return 0.95
        elif temp <= low - spread:
            return 0.05
        else:
            return round(0.5 + 0.45 * (temp - low) / spread, 4)
    else:
        # Range bucket [low, high)
        bucket_mid = (low + high) / 2
        bucket_width = high - low
        distance = abs(temp - bucket_mid)
        if distance < bucket_width / 2:
            return round(min(0.85, 0.4 + 0.45 * (1 - distance / spread)), 4)
        elif distance < spread:
            return round(max(0.02, 0.4 * (1 - distance / spread)), 4)
        else:
            return 0.02


# ---------------------------------------------------------------------------
# Fusion engine
# ---------------------------------------------------------------------------

def fuse_forecast(
    lat: float, lon: float, city: str, month: int,
    low: float, high: Optional[float],
    days_ahead: int = 1, unit: str = "f", temp_type: str = "max",
    liquidity_score: float = 0.5,
) -> Tuple[float, float, dict]:
    """Run multi-model fusion and return (fused_prob, confidence, details).

    temp_type: "max" for daily high, "min" for daily low.
    details dict contains per-model info for logging/debugging.
    """
    weights = dict(FUSION_WEIGHTS)
    details = {}

    # --- Model 1: Open-Meteo Ensemble (30 members) ---
    cache_key_ens = (round(lat, 2), round(lon, 2), temp_type, days_ahead, unit)
    ensemble_temps = fcache.get("ensemble", *cache_key_ens)
    if ensemble_temps is None:
        if temp_type == "min":
            ensemble_temps = get_ensemble_min_temps(lat, lon, days_ahead=days_ahead, unit=unit)
        else:
            ensemble_temps = get_ensemble_max_temps(lat, lon, days_ahead=days_ahead, unit=unit)
        if ensemble_temps:
            fcache.put("ensemble", *cache_key_ens, value=ensemble_temps)
    bias_ens, n_ens = get_bias(city, month, "ensemble")
    if bias_ens and n_ens >= 5:
        ensemble_temps = [round(t - bias_ens, 1) for t in ensemble_temps]
    ensemble_prob = get_bucket_prob(ensemble_temps, low, high)
    ensemble_spread = max(ensemble_temps) - min(ensemble_temps) if ensemble_temps else 99
    ensemble_mean = round(sum(ensemble_temps) / len(ensemble_temps), 1) if ensemble_temps else None
    details["ensemble"] = {
        "prob": ensemble_prob, "spread": round(ensemble_spread, 1),
        "bias": round(bias_ens, 1), "n": n_ens, "temps_count": len(ensemble_temps),
        "temp": ensemble_mean,
    }

    # --- Model 2: NOAA NWS Point Forecast ---
    cache_key_noaa = (round(lat, 2), round(lon, 2), temp_type, days_ahead, unit)
    noaa_temp = fcache.get("noaa", *cache_key_noaa)
    if noaa_temp is None:
        noaa_temp = get_noaa_point_forecast(lat, lon, days_ahead=days_ahead, unit=unit, temp_type=temp_type)
        if noaa_temp is not None:
            fcache.put("noaa", *cache_key_noaa, value=noaa_temp)
    noaa_prob = None
    if noaa_temp is not None:
        bias_noaa, n_noaa = get_bias(city, month, "noaa")
        if bias_noaa and n_noaa >= 5:
            noaa_temp = round(noaa_temp - bias_noaa, 1)
        noaa_prob = _deterministic_bucket_prob(noaa_temp, low, high)
        details["noaa"] = {"prob": noaa_prob, "temp": noaa_temp, "bias": round(bias_noaa, 1), "n": n_noaa}
    else:
        details["noaa"] = {"prob": None, "error": "unavailable"}

    # --- Model 3: GFS+HRRR Seamless ---
    cache_key_hrrr = (round(lat, 2), round(lon, 2), temp_type, days_ahead, unit)
    hrrr_temp = fcache.get("hrrr", *cache_key_hrrr)
    if hrrr_temp is None:
        hrrr_temp = get_hrrr_forecast(lat, lon, days_ahead=days_ahead, unit=unit, temp_type=temp_type)
        if hrrr_temp is not None:
            fcache.put("hrrr", *cache_key_hrrr, value=hrrr_temp)
    hrrr_prob = None
    if hrrr_temp is not None:
        bias_hrrr, n_hrrr = get_bias(city, month, "hrrr")
        if bias_hrrr and n_hrrr >= 5:
            hrrr_temp = round(hrrr_temp - bias_hrrr, 1)
        hrrr_prob = _deterministic_bucket_prob(hrrr_temp, low, high)
        details["hrrr"] = {"prob": hrrr_prob, "temp": hrrr_temp, "bias": round(bias_hrrr, 1), "n": n_hrrr}
    else:
        details["hrrr"] = {"prob": None, "error": "unavailable"}

    # --- Weighted fusion (redistribute weight from missing models) ---
    active = {}
    if ensemble_prob is not None:
        active["ensemble"] = ensemble_prob
    if noaa_prob is not None:
        active["noaa"] = noaa_prob
    if hrrr_prob is not None:
        active["hrrr"] = hrrr_prob

    if not active:
        return ensemble_prob, 0, details

    total_weight = sum(weights[k] for k in active)
    fused_prob = sum(weights[k] * active[k] / total_weight for k in active)
    fused_prob = round(fused_prob, 4)
    details["fused_prob"] = fused_prob
    details["models_used"] = len(active)

    # --- Confidence score (0–100) — continuous weighted scoring ---
    # Each component produces a 0-1 signal; weighted sum maps to [55, 100].
    # This feeds directly into the sigmoid Kelly curve, so smooth gradations
    # matter more than stepped thresholds.

    # Component 1: Model agreement (0-1) — how close are point forecasts?
    point_temps = []
    if ensemble_temps:
        point_temps.append(sum(ensemble_temps) / len(ensemble_temps))
    if noaa_temp is not None:
        point_temps.append(noaa_temp)
    if hrrr_temp is not None:
        point_temps.append(hrrr_temp)
    if len(point_temps) >= 2:
        temp_range = max(point_temps) - min(point_temps)
        # 0°F range → 1.0, 10°F range → 0.0, linear
        agreement = max(0.0, min(1.0, 1.0 - temp_range / 10.0))
    else:
        agreement = 0.5  # single model, neutral

    # Component 2: Ensemble spread (0-1) — tighter = more confident
    # 0°F spread → 1.0, 12°F spread → 0.0, linear
    spread_norm = max(0.0, min(1.0, 1.0 - ensemble_spread / 12.0))

    # Component 3: Bias data available (0-1)
    bias_counts = [get_bias(city, month, m)[1] for m in ["ensemble", "noaa", "hrrr"]]
    best_bias_n = max(bias_counts) if bias_counts else 0
    # 0 samples → 0, 10 → 0.5, 30+ → 1.0
    bias_available = min(1.0, best_bias_n / 30.0)

    # Component 4: CSGD/model fit quality — always 1.0 for temp (ensemble is primary)
    csgd_success = 1.0

    # Component 5: NWS agreement strength (0-1, not just presence)
    if noaa_prob is not None and ensemble_prob is not None:
        nws_agreement = max(0.0, min(1.0, 1.0 - abs(ensemble_prob - noaa_prob) / 0.3))
    else:
        nws_agreement = 0.0

    confidence = _calculate_confidence(
        agreement, spread_norm, bias_available, csgd_success,
        nws_agreement, horizon_days=days_ahead,
        liquidity_score=liquidity_score,
    )
    details["confidence"] = confidence

    return fused_prob, confidence, details


def fuse_precip_forecast(
    lat: float, lon: float, city: str, month: int,
    threshold: float, forecast_days: Optional[int] = None,
    liquidity_score: float = 0.5,
) -> Tuple[float, float, dict]:
    """Precipitation fusion. Returns (fused_prob, confidence, details).

    Same return shape as fuse_forecast() for temperature so the scanner
    edge-computation logic works identically.
    """
    weights = dict(PRECIP_FUSION_WEIGHTS)
    details = {}

    # --- Model 1: Open-Meteo Ensemble (30-member precip) ---
    cache_key = (round(lat, 2), round(lon, 2), "precip", forecast_days or 1)
    ensemble_precip = fcache.get("ensemble_precip", *cache_key)
    if ensemble_precip is None:
        ensemble_precip = get_ensemble_precip(lat, lon, forecast_days=forecast_days)
        if ensemble_precip:
            fcache.put("ensemble_precip", *cache_key, value=ensemble_precip)

    # Get NWS PoP for blended p_dry
    nws_pop, nws_qpf = get_nws_precip_forecast(lat, lon)

    # CSGD model (primary)
    csgd_result = gamma_precip_prob(ensemble_precip, threshold=threshold, nws_pop=nws_pop)

    # Bias correction for ensemble
    bias_ens, n_ens = get_bias(city, month, "ensemble_precip")

    ensemble_prob = csgd_result.prob_above
    details["ensemble"] = {
        "prob": ensemble_prob,
        "fraction_above": csgd_result.fraction_above,
        "p_dry": csgd_result.p_dry,
        "shape": csgd_result.shape,
        "scale": csgd_result.scale,
        "method": csgd_result.method,
        "bias": round(bias_ens, 3),
        "n": n_ens,
        "members_count": len(ensemble_precip),
    }

    # --- Model 2: NWS PoP + QPF ---
    if threshold <= 0.0:
        noaa_prob = nws_pop
    elif nws_qpf > 0:
        ratio = min(1.0, nws_qpf / max(threshold, 0.01))
        noaa_prob = nws_pop * ratio
    else:
        noaa_prob = nws_pop * 0.3

    noaa_prob = max(0.0, min(1.0, noaa_prob))
    bias_noaa, n_noaa = get_bias(city, month, "noaa_precip")
    details["noaa"] = {"prob": noaa_prob, "pop": nws_pop, "qpf": nws_qpf,
                       "bias": round(bias_noaa, 3), "n": n_noaa}

    # --- Weighted fusion (ensemble + NWS; HRRR slot reserved for future) ---
    active = {"ensemble": ensemble_prob}
    if noaa_prob is not None:
        active["noaa"] = noaa_prob

    total_weight = sum(weights.get(k, 0) for k in active)
    if total_weight == 0:
        fused_prob = ensemble_prob
    else:
        fused_prob = sum(weights.get(k, 0) * active[k] / total_weight for k in active)
    fused_prob = round(max(0.0, min(1.0, fused_prob)), 4)

    details["fused_prob"] = fused_prob
    details["models_used"] = len(active)

    # --- Confidence scoring (0-100) — continuous weighted, same as temp ---

    # Component 1: Model agreement (0-1) — ensemble vs NWS probability agreement
    if noaa_prob is not None:
        prob_diff = abs(ensemble_prob - noaa_prob)
        agreement = max(0.0, min(1.0, 1.0 - prob_diff / 0.5))
    else:
        agreement = 0.5

    # Component 2: Ensemble consensus (0-1) — how decisive is the ensemble?
    fa = csgd_result.fraction_above
    # fa near 0 or 1 = strong consensus; fa near 0.5 = uncertain
    spread_norm = 2.0 * abs(fa - 0.5)  # 0=split, 1=unanimous

    # Component 3: Bias data available (0-1)
    bias_counts = [get_bias(city, month, m)[1] for m in ["ensemble_precip", "noaa_precip"]]
    best_bias_n = max(bias_counts) if bias_counts else 0
    bias_available = min(1.0, best_bias_n / 30.0)

    # Component 4: CSGD fit quality — continuous: 1.0 for csgd, 0.3 for empirical fallback
    csgd_success = 1.0 if csgd_result.method == "csgd" else 0.3

    # Component 5: NWS agreement strength (0-1, not just presence)
    if noaa_prob is not None and ensemble_prob is not None:
        nws_agreement = max(0.0, min(1.0, 1.0 - abs(ensemble_prob - noaa_prob) / 0.3))
    else:
        nws_agreement = 0.0

    confidence = _calculate_confidence(
        agreement, spread_norm, bias_available, csgd_success,
        nws_agreement, horizon_days=forecast_days or 1,
        liquidity_score=liquidity_score,
    )
    details["confidence"] = confidence

    return fused_prob, confidence, details
