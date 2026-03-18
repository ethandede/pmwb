"""
Multi-Model Fusion + Bias Correction + Nowcasting

Blends 3 independent forecast sources for sharper probability estimates.
UPDATED (Mar 2026): Uses proper Normal distribution for deterministic buckets.
No more negatives possible. Built with Super Heavy Grok.
"""

import math
import os
import sqlite3
import requests
from datetime import datetime, timezone
from weather.http import get as http_get
from typing import List, Optional, Tuple, Dict
from weather.forecast import get_ensemble_max_temps, get_ensemble_min_temps, get_bucket_prob
from weather.forecast import get_ensemble_precip, get_nws_precip_forecast
from weather import cache as fcache
from weather.precip_model import gamma_precip_prob
from config import BIAS_DB_PATH, FUSION_WEIGHTS, PRECIP_FUSION_WEIGHTS

# --- Forecast source health tracking ---
_model_fail_counts: dict[str, int] = {}
_ALERT_THRESHOLD = 3  # alert after 3 consecutive failures per model


def _track_model_failures(down: set[str], active: dict[str, float]):
    """Track which models are failing and alert after sustained outages."""
    all_models = {"ensemble", "noaa", "hrrr", "ecmwf", "visualcrossing"}
    for model in all_models:
        if model in down:
            _model_fail_counts[model] = _model_fail_counts.get(model, 0) + 1
            if _model_fail_counts[model] == _ALERT_THRESHOLD:
                try:
                    from alerts.telegram_alert import send_alert
                    active_names = ", ".join(sorted(active.keys()))
                    send_alert(
                        f"Forecast Source Down: {model}",
                        f"{model} has failed {_ALERT_THRESHOLD} consecutive times.\n"
                        f"Active models ({len(active)}): {active_names}\n"
                        f"Weights auto-rebalanced across remaining sources.",
                        dedup_key=f"model_down_{model}",
                    )
                except Exception:
                    pass
        else:
            _model_fail_counts[model] = 0  # reset on success


# ===========================================================================
# NEW ROBUST PROBABILITY ENGINE (replaces old fragile function)
# ===========================================================================

def normal_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """Pure-Python normal CDF using erf. No external dependencies."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    z = (x - mu) / (sigma * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def _deterministic_bucket_prob(
    temp: float, low: Optional[float], high: Optional[float], spread: float = 4.5
) -> float:
    """
    NEW SAFE VERSION — March 2026
    Assumes actual temp ~ Normal(forecast=temp, std=spread).
    Handles >=low, <high, and [low, high) buckets.
    Mathematically guaranteed [0.01, 0.99].
    """
    if high is None and low is not None:
        # >= low bucket
        prob = 1.0 - normal_cdf(low, temp, spread)
    elif low is None and high is not None:
        # < high bucket
        prob = normal_cdf(high, temp, spread)
    elif low is not None and high is not None:
        # [low, high) range bucket
        prob = normal_cdf(high, temp, spread) - normal_cdf(low, temp, spread)
    else:
        prob = 0.5

    return round(max(0.01, min(0.99, prob)), 4)


def fuse_model_probs(model_probs: Dict[str, float], weights: Optional[Dict[str, float]] = None) -> float:
    """Safe weighted fusion with validation (never outside [0.01, 0.99])."""
    if not model_probs:
        return 0.50

    # Defensive logging — will almost never fire again
    for name, p in model_probs.items():
        if not (0.0 <= p <= 1.0):
            print(f"  [BAD INPUT] {name} = {p:.4f}")

    if weights is None:
        weights = {k: FUSION_WEIGHTS.get(k, 1.0) for k in model_probs}

    total_weight = sum(weights.get(k, 0) for k in model_probs)
    if total_weight == 0:
        fused = sum(model_probs.values()) / len(model_probs)
    else:
        fused = sum(weights.get(k, 0) * model_probs.get(k, 0) for k in model_probs) / total_weight

    clamped = max(0.01, min(0.99, fused))
    if abs(clamped - fused) > 0.001:
        print(f"  [CLAMP] fused_prob={fused:.4f} → {clamped:.4f}")

    return round(clamped, 4)


# ===========================================================================
# Everything below this line is your original code (only tiny fusion update)
# ===========================================================================

def _get_liquidity_score(market: dict) -> float:
    """Normalize volume + open interest to [0, 1] for confidence scoring.
    Typical Kalshi weather markets: $500 ~ 0.2, $5k ~ 0.6, $50k+ ~ 1.0"""
    volume = float(market.get("volume_24h_fp", 0) or 0)
    oi = float(market.get("open_interest_fp", 0) or 0)
    combined = volume + (oi * 0.6)
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
    """Nonlinear continuous confidence (40-100). [your original function — unchanged]"""
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
# SQLite bias table (unchanged)
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
    conn = _get_db()
    row = conn.execute(
        "SELECT avg_bias, sample_count FROM bias WHERE city=? AND month=? AND model=?",
        (city, month, model),
    ).fetchone()
    conn.close()
    return (row[0], row[1]) if row else (0.0, 0)


def update_bias(city: str, month: int, model: str, forecast_high: float, actual_high: float):
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
# NOAA NWS Point Forecast (unchanged)
# ---------------------------------------------------------------------------

_nws_cache = {}

NWS_HEADERS = {"User-Agent": "WeatherEdgeBot/1.0 (ethanede@gmail.com)", "Accept": "application/geo+json"}


def get_noaa_point_forecast(lat: float, lon: float, days_ahead: int = 1, unit: str = "f", temp_type: str = "max") -> Optional[float]:
    try:
        cache_key = (round(lat, 4), round(lon, 4))
        if cache_key not in _nws_cache:
            points_url = f"https://api.weather.gov/points/{lat},{lon}"
            r = http_get(points_url, headers=NWS_HEADERS, timeout=10)
            r.raise_for_status()
            forecast_url = r.json()["properties"]["forecast"]
            _nws_cache[cache_key] = forecast_url
        else:
            forecast_url = _nws_cache[cache_key]

        r = http_get(forecast_url, headers=NWS_HEADERS, timeout=10)
        r.raise_for_status()
        periods = r.json()["properties"]["periods"]

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

        if unit == "c" and temp_unit == "F":
            temp = round((temp - 32) * 5 / 9, 1)
        elif unit == "f" and temp_unit == "C":
            temp = round(temp * 9 / 5 + 32, 1)

        return temp
    except Exception as e:
        print(f"  NOAA forecast error: {e}")
        return None


# ---------------------------------------------------------------------------
# HRRR (via Open-Meteo gfs_hrrr) — 0-48h high-res CONUS forecast
# ---------------------------------------------------------------------------

_HRRR_LOCAL = "http://localhost:8080/v1/forecast"
_HRRR_PUBLIC = "https://api.open-meteo.com/v1/forecast"


def get_hrrr_forecast(lat: float, lon: float, days_ahead: int = 1, unit: str = "f", temp_type: str = "max") -> Optional[float]:
    unit_param = "fahrenheit" if unit == "f" else "celsius"
    daily_var = "temperature_2m_max" if temp_type == "max" else "temperature_2m_min"
    params = (
        f"?latitude={lat}&longitude={lon}"
        f"&daily={daily_var}"
        f"&models=gfs_hrrr"
        f"&temperature_unit={unit_param}"
        f"&timezone=auto"
        f"&forecast_days={days_ahead + 2}"
    )
    for base in (_HRRR_LOCAL, _HRRR_PUBLIC):
        try:
            r = http_get(f"{base}{params}", timeout=10)
            r.raise_for_status()
            data = r.json()
            temps = data.get("daily", {}).get(daily_var, [])
            if len(temps) > days_ahead and temps[days_ahead] is not None:
                return round(float(temps[days_ahead]), 1)
        except Exception as e:
            print(f"  HRRR forecast error ({base}): {e}")
    return None


# ---------------------------------------------------------------------------
# ECMWF IFS (European model — independent from GFS)
# ---------------------------------------------------------------------------

def get_ecmwf_forecast(lat: float, lon: float, days_ahead: int = 1, unit: str = "f", temp_type: str = "max") -> Optional[float]:
    """Fetch ECMWF IFS forecast via Open-Meteo.

    The IFS is Europe's primary global model — architecturally independent
    from the American GFS. Adding it gives genuine model diversity.
    """
    try:
        unit_param = "fahrenheit" if unit == "f" else "celsius"
        daily_var = f"temperature_2m_{temp_type}"
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&daily={daily_var}"
            f"&models=ecmwf_ifs025"
            f"&temperature_unit={unit_param}"
            f"&timezone=auto"
            f"&forecast_days={days_ahead + 2}"
        )
        r = http_get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        temps = data.get("daily", {}).get(daily_var, [])
        if len(temps) > days_ahead and temps[days_ahead] is not None:
            return round(float(temps[days_ahead]), 1)
        return None
    except Exception as e:
        print(f"  ECMWF IFS forecast error: {e}")
        return None


# ---------------------------------------------------------------------------
# Visual Crossing (Weather Underground data source)
# ---------------------------------------------------------------------------

def get_visualcrossing_forecast(lat: float, lon: float, days_ahead: int = 1, unit: str = "f", temp_type: str = "max") -> Optional[float]:
    """Fetch forecast from Visual Crossing API (Weather Underground data).

    Returns the predicted high or low temperature for the target day.
    Free tier: 1000 calls/day — cached via fcache to stay well under.
    """
    from config import VISUAL_CROSSING_API_KEY
    if not VISUAL_CROSSING_API_KEY:
        return None
    try:
        unit_group = "us" if unit == "f" else "metric"
        temp_field = "tempmax" if temp_type == "max" else "tempmin"
        r = http_get(
            f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
            f"/{lat},{lon}/next3days",
            params={
                "key": VISUAL_CROSSING_API_KEY,
                "unitGroup": unit_group,
                "include": "days",
                "elements": f"{temp_field}",
            },
            timeout=10,
        )
        r.raise_for_status()
        days = r.json().get("days", [])
        if len(days) > days_ahead and days[days_ahead].get(temp_field) is not None:
            return round(float(days[days_ahead][temp_field]), 1)
        return None
    except Exception as e:
        print(f"  Visual Crossing forecast error: {e}")
        return None


# ---------------------------------------------------------------------------
# Fusion engine — UPDATED with safe functions
# ---------------------------------------------------------------------------

def fuse_forecast(
    lat: float, lon: float, city: str, month: int,
    low: float, high: Optional[float],
    days_ahead: int = 1, unit: str = "f", temp_type: str = "max",
    liquidity_score: float = 0.5,
) -> Tuple[float, float, dict]:
    """Run multi-model fusion using the new robust probability engine."""
    weights = dict(FUSION_WEIGHTS)
    details = {}

    # City-specific temperature variability (replaces hardcoded 4.5°F)
    from weather.climate import get_temp_std
    try:
        temp_spread = get_temp_std(city, month, lat, lon)
    except Exception:
        temp_spread = 8.0  # conservative fallback
    details["temp_spread"] = temp_spread

    # --- Model 1: Open-Meteo Ensemble (unchanged) ---
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
    if bias_ens and n_ens >= 30:
        ensemble_temps = [round(t - bias_ens, 1) for t in ensemble_temps]
    if ensemble_temps:
        ensemble_prob = get_bucket_prob(ensemble_temps, low, high)
        ensemble_spread = max(ensemble_temps) - min(ensemble_temps)
        ensemble_mean = round(sum(ensemble_temps) / len(ensemble_temps), 1)
        details["ensemble"] = {
            "prob": ensemble_prob, "spread": round(ensemble_spread, 1),
            "bias": round(bias_ens, 1), "n": n_ens, "temps_count": len(ensemble_temps),
            "temp": ensemble_mean,
        }
    else:
        ensemble_prob = None
        ensemble_spread = None
        ensemble_mean = None
        details["ensemble"] = {"prob": None, "error": "unavailable (429 or no data)"}

    # --- Model 2: NOAA NWS (now uses safe bucket prob) ---
    cache_key_noaa = (round(lat, 2), round(lon, 2), temp_type, days_ahead, unit)
    noaa_temp = fcache.get("noaa", *cache_key_noaa)
    if noaa_temp is None:
        noaa_temp = get_noaa_point_forecast(lat, lon, days_ahead=days_ahead, unit=unit, temp_type=temp_type)
        if noaa_temp is not None:
            fcache.put("noaa", *cache_key_noaa, value=noaa_temp)
    noaa_prob = None
    if noaa_temp is not None:
        bias_noaa, n_noaa = get_bias(city, month, "noaa")
        if bias_noaa and n_noaa >= 30:
            noaa_temp = round(noaa_temp - bias_noaa, 1)
        noaa_prob = _deterministic_bucket_prob(noaa_temp, low, high, spread=temp_spread)
        details["noaa"] = {"prob": noaa_prob, "temp": noaa_temp, "bias": round(bias_noaa, 1), "n": n_noaa}
    else:
        details["noaa"] = {"prob": None, "error": "unavailable"}

    # --- Model 3: GFS+HRRR (now uses safe bucket prob) ---
    cache_key_hrrr = (round(lat, 2), round(lon, 2), temp_type, days_ahead, unit)
    hrrr_temp = fcache.get("hrrr", *cache_key_hrrr)
    if hrrr_temp is None:
        hrrr_temp = get_hrrr_forecast(lat, lon, days_ahead=days_ahead, unit=unit, temp_type=temp_type)
        if hrrr_temp is not None:
            fcache.put("hrrr", *cache_key_hrrr, value=hrrr_temp)
    hrrr_prob = None
    if hrrr_temp is not None:
        bias_hrrr, n_hrrr = get_bias(city, month, "hrrr")
        if bias_hrrr and n_hrrr >= 30:
            hrrr_temp = round(hrrr_temp - bias_hrrr, 1)
        hrrr_prob = _deterministic_bucket_prob(hrrr_temp, low, high, spread=temp_spread)
        details["hrrr"] = {"prob": hrrr_prob, "temp": hrrr_temp, "bias": round(bias_hrrr, 1), "n": n_hrrr}
    else:
        details["hrrr"] = {"prob": None, "error": "unavailable"}

    # --- Model 4: ECMWF IFS (European model) ---
    cache_key_ecmwf = (round(lat, 2), round(lon, 2), temp_type, days_ahead, unit)
    ecmwf_temp = fcache.get("ecmwf", *cache_key_ecmwf)
    if ecmwf_temp is None and not fcache.has("ecmwf", *cache_key_ecmwf):
        ecmwf_temp = get_ecmwf_forecast(lat, lon, days_ahead=days_ahead, unit=unit, temp_type=temp_type)
        # Cache both successes and failures
        fcache.put("ecmwf", *cache_key_ecmwf, value=ecmwf_temp)
    ecmwf_prob = None
    if ecmwf_temp is not None:
        bias_ecmwf, n_ecmwf = get_bias(city, month, "ecmwf")
        if bias_ecmwf and n_ecmwf >= 30:
            ecmwf_temp = round(ecmwf_temp - bias_ecmwf, 1)
        ecmwf_prob = _deterministic_bucket_prob(ecmwf_temp, low, high, spread=temp_spread)
        details["ecmwf"] = {"prob": ecmwf_prob, "temp": ecmwf_temp, "bias": round(bias_ecmwf, 1), "n": n_ecmwf}
    else:
        details["ecmwf"] = {"prob": None, "error": "unavailable"}

    # --- Model 5: Visual Crossing (Weather Underground) ---
    cache_key_vc = (round(lat, 2), round(lon, 2), temp_type, days_ahead, unit)
    vc_temp = fcache.get("visualcrossing", *cache_key_vc)
    if vc_temp is None and not fcache.has("visualcrossing", *cache_key_vc):
        vc_temp = get_visualcrossing_forecast(lat, lon, days_ahead=days_ahead, unit=unit, temp_type=temp_type)
        # Cache both successes and failures — prevents 429 retry storms
        fcache.put("visualcrossing", *cache_key_vc, value=vc_temp)
    vc_prob = None
    if vc_temp is not None:
        bias_vc, n_vc = get_bias(city, month, "visualcrossing")
        if bias_vc and n_vc >= 30:
            vc_temp = round(vc_temp - bias_vc, 1)
        vc_prob = _deterministic_bucket_prob(vc_temp, low, high, spread=temp_spread)
        details["visualcrossing"] = {"prob": vc_prob, "temp": vc_temp, "bias": round(bias_vc, 1), "n": n_vc}
    else:
        details["visualcrossing"] = {"prob": None, "error": "unavailable"}

    # --- SAFE FUSION ---
    active = {}
    if ensemble_prob is not None:
        active["ensemble"] = ensemble_prob
    if noaa_prob is not None:
        active["noaa"] = noaa_prob
    if hrrr_prob is not None:
        active["hrrr"] = hrrr_prob
    if ecmwf_prob is not None:
        active["ecmwf"] = ecmwf_prob
    if vc_prob is not None:
        active["visualcrossing"] = vc_prob

    if not active:
        # All models failed — refuse to trade (confidence 0 blocks all gates)
        details["all_models_down"] = True
        return 0.50, 0.0, details

    # Alert on degraded model coverage
    all_models = {"ensemble", "noaa", "hrrr", "ecmwf", "visualcrossing"}
    down_models = all_models - set(active.keys())
    if down_models:
        _track_model_failures(down_models, active)

    fused_prob = fuse_model_probs(active, weights)
    details["fused_prob"] = fused_prob
    details["models_used"] = len(active)
    details["models_down"] = sorted(down_models) if down_models else []

    # --- Confidence score (your original code — unchanged) ---
    point_temps = []
    if ensemble_temps:
        point_temps.append(sum(ensemble_temps) / len(ensemble_temps))
    if noaa_temp is not None:
        point_temps.append(noaa_temp)
    if hrrr_temp is not None:
        point_temps.append(hrrr_temp)
    if ecmwf_temp is not None:
        point_temps.append(ecmwf_temp)
    if vc_temp is not None:
        point_temps.append(vc_temp)
    if len(point_temps) >= 2:
        temp_range = max(point_temps) - min(point_temps)
        agreement = max(0.0, min(1.0, 1.0 - temp_range / 10.0))
    else:
        agreement = 0.5

    if ensemble_spread is not None:
        spread_norm = max(0.0, min(1.0, 1.0 - ensemble_spread / 12.0))
    elif len(point_temps) >= 2:
        # Model disagreement * 3 approximates ensemble member spread
        inferred_spread = temp_range * 3.0
        spread_norm = max(0.0, min(1.0, 1.0 - inferred_spread / 12.0))
    else:
        spread_norm = 0.3  # conservative default: single model only

    bias_counts = [get_bias(city, month, m)[1] for m in ["ensemble", "noaa", "hrrr", "ecmwf", "visualcrossing"]]
    best_bias_n = max(bias_counts) if bias_counts else 0
    bias_available = min(1.0, best_bias_n / 30.0)

    csgd_success = 1.0

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


# ---------------------------------------------------------------------------
# ERCOT Solar / Power Price Signal
# ---------------------------------------------------------------------------

def get_ercot_solar_signal(
    lat: float, lon: float,
    hub_key: str = "",
    solar_sensitivity: float = 0.15,
    hours_ahead: int = 24,
    ercot_data: dict = None,
) -> dict:
    """Solar irradiance + ERCOT price -> fair-price trading signal.

    Uses per-hub solar sensitivity and load forecast to estimate
    whether the current hub price is above or below fair value.
    """
    from config import (
        VISUAL_CROSSING_API_KEY, ERCOT_SEASONAL_NORMS,
        ERCOT_LOAD_SENSITIVITY,
    )

    # 1. Seasonal norms for current month
    month = datetime.now(timezone.utc).month
    norms = ERCOT_SEASONAL_NORMS.get(month)
    if norms is None:
        try:
            from alerts.telegram_alert import send_alert
            send_alert("ERCOT Norms Missing", f"No seasonal norms for month {month}",
                       dedup_key="ercot_norms_missing")
        except Exception:
            pass
        norms = {"solar": 18.0, "load": 50_000}
    norm_solar = norms["solar"]
    norm_load = norms["load"]

    # 2a. Visual Crossing solrad (primary)
    vc_solrad = None
    if VISUAL_CROSSING_API_KEY:
        try:
            vc_r = http_get(
                f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
                f"/{lat},{lon}/next3days",
                params={"key": VISUAL_CROSSING_API_KEY, "unitGroup": "metric",
                        "include": "days", "elements": "solarenergy"},
                timeout=10,
            )
            vc_r.raise_for_status()
            vc_days = vc_r.json().get("days", [])
            target_idx = min(hours_ahead // 24, len(vc_days) - 1)
            if vc_days and vc_days[target_idx].get("solarenergy") is not None:
                vc_solrad = float(vc_days[target_idx]["solarenergy"])
        except Exception as e:
            print(f"  Visual Crossing solar error: {e}")

    # 2b. Open-Meteo solrad (cross-reference for confidence)
    om_solrad = None
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&daily=shortwave_radiation_sum"
            f"&forecast_days=3"
            f"&timezone=auto"
        )
        r = http_get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        radiation = data.get("daily", {}).get("shortwave_radiation_sum", [])
        target_idx = min(hours_ahead // 24, len(radiation) - 1)
        raw_om = radiation[target_idx] if radiation else None

        if raw_om is not None:
            # Unit validation — skip agreement check if unit unrecognized
            unit = data.get("daily_units", {}).get("shortwave_radiation_sum", "")
            if "kJ" in unit:
                om_solrad = raw_om / 1000.0
            elif "Wh" in unit:
                om_solrad = raw_om * 0.0036
            elif "MJ" in unit:
                om_solrad = raw_om
            else:
                # Unrecognized unit — log warning, skip agreement check
                print(f"  Open-Meteo unknown solar unit: {unit!r}, skipping agreement")
                om_solrad = None  # don't use for agreement
    except Exception as e:
        print(f"  Open-Meteo solar error: {e}")

    # 2c. Pick primary solrad: VC preferred, OM fallback, then seasonal norm
    if vc_solrad is not None:
        expected_solrad = vc_solrad
    elif om_solrad is not None:
        expected_solrad = om_solrad
    else:
        expected_solrad = norm_solar  # no data = no solar signal

    # 3. ERCOT market data
    if ercot_data is not None:
        hub_price = float(ercot_data.get("hub_price", ercot_data.get("price", 40.0)))
        actual_solar_mw = float(ercot_data.get("solar_mw", 0.0))
        load_forecast = float(ercot_data.get("load_forecast", norm_load))
    else:
        hub_price = 40.0
        actual_solar_mw = 12000.0
        load_forecast = norm_load

    # 4. Fair price model
    solar_impact = solar_sensitivity * (norm_solar - expected_solrad) / norm_solar
    load_impact = ERCOT_LOAD_SENSITIVITY * (load_forecast - norm_load) / norm_load
    edge = round(solar_impact + load_impact, 4)

    # 5. Signal direction
    if edge > 0:
        signal = "LONG"
    elif edge < 0:
        signal = "SHORT"
    else:
        signal = "NEUTRAL"

    # 6. Confidence: base + agreement + deviation
    base_conf = 30
    agreement_bonus = 0
    if vc_solrad is not None and om_solrad is not None:
        if abs(vc_solrad - om_solrad) <= 2.0:
            agreement_bonus = 20
    price_deviation_bonus = min(30, abs(edge) * 300)
    confidence = int(min(90, max(30, base_conf + agreement_bonus + price_deviation_bonus)))

    return {
        "signal": signal,
        "edge": round(edge, 4),
        "expected_solrad_mjm2": round(expected_solrad, 1),
        "current_ercot_price": round(hub_price, 1),
        "actual_solar_mw": round(actual_solar_mw, 0),
        "confidence": confidence,
    }


def fuse_precip_forecast(
    lat: float, lon: float, city: str, month: int,
    threshold: float, forecast_days: Optional[int] = None,
    liquidity_score: float = 0.5,
) -> Tuple[float, float, dict]:
    """Precipitation fusion with month-to-date adjustment.

    For monthly contracts (forecast_days > 1), fetches observed precipitation
    so far this month and subtracts from the threshold. This way the ensemble
    only needs to forecast the REMAINING precipitation, not the full month.
    """
    weights = dict(PRECIP_FUSION_WEIGHTS)
    details = {}

    # --- MTD adjustment for monthly contracts ---
    mtd_precip = 0.0
    effective_threshold = threshold
    if forecast_days and forecast_days > 1 and threshold > 0:
        from weather.forecast import get_observed_mtd_precip
        mtd_raw = get_observed_mtd_precip(lat, lon)
        if mtd_raw is None:
            # MTD data unavailable (API down / rate limited).
            # Trading without MTD is dangerous — a false zero leads to
            # NO bets on thresholds already exceeded. Bail with low confidence.
            details["mtd_error"] = "unavailable"
            details["fused_prob"] = 0.50
            details["models_used"] = 0
            details["confidence"] = 0.0
            return 0.50, 0.0, details
        mtd_precip = mtd_raw
        effective_threshold = max(0.0, threshold - mtd_precip)
        details["mtd_observed_inches"] = mtd_precip
        details["original_threshold"] = threshold
        details["effective_threshold"] = effective_threshold

    # Short-circuit: if MTD already exceeds threshold, probability is ~1.0
    if mtd_precip >= threshold and threshold > 0:
        details["fused_prob"] = 0.99
        details["models_used"] = 0
        details["short_circuit"] = "mtd_exceeds_threshold"
        confidence = 90.0
        details["confidence"] = confidence
        return 0.99, confidence, details

    cache_key = (round(lat, 2), round(lon, 2), "precip", forecast_days or 1)
    ensemble_precip = fcache.get("ensemble_precip", *cache_key)
    if ensemble_precip is None:
        ensemble_precip = get_ensemble_precip(lat, lon, forecast_days=forecast_days)
        if ensemble_precip:
            fcache.put("ensemble_precip", *cache_key, value=ensemble_precip)

    # If ensemble is empty (API down), refuse to trade precip
    if not ensemble_precip:
        details["ensemble"] = {"prob": None, "error": "unavailable"}
        details["fused_prob"] = 0.50
        details["models_used"] = 0
        details["confidence"] = 0.0
        return 0.50, 0.0, details

    nws_pop, nws_qpf = get_nws_precip_forecast(lat, lon)
    # NWS returns (None, None) on failure — treat as unavailable
    if nws_pop is None:
        nws_pop = 0.5  # neutral fallback for gamma blending only
        nws_qpf = 0.0

    # Use effective_threshold (adjusted for MTD) instead of raw threshold
    csgd_result = gamma_precip_prob(ensemble_precip, threshold=effective_threshold, nws_pop=nws_pop)

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

    # NWS: scale QPF by remaining days for monthly contracts
    noaa_prob = None
    if nws_qpf is not None:
        if threshold <= 0.0:
            noaa_prob = nws_pop
        elif nws_qpf > 0:
            remaining_days = forecast_days if forecast_days and forecast_days > 1 else 1
            estimated_remaining_qpf = nws_qpf * remaining_days * 0.5
            ratio = min(1.0, estimated_remaining_qpf / max(effective_threshold, 0.01))
            noaa_prob = nws_pop * ratio
        else:
            noaa_prob = nws_pop * 0.3
        noaa_prob = max(0.0, min(1.0, noaa_prob))

    bias_noaa, n_noaa = get_bias(city, month, "noaa_precip")
    details["noaa"] = {"prob": noaa_prob, "pop": nws_pop, "qpf": nws_qpf,
                       "bias": round(bias_noaa, 3), "n": n_noaa}

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

    # confidence scoring (unchanged)
    if noaa_prob is not None:
        prob_diff = abs(ensemble_prob - noaa_prob)
        agreement = max(0.0, min(1.0, 1.0 - prob_diff / 0.5))
    else:
        agreement = 0.5

    fa = csgd_result.fraction_above
    spread_norm = 2.0 * abs(fa - 0.5)

    bias_counts = [get_bias(city, month, m)[1] for m in ["ensemble_precip", "noaa_precip"]]
    best_bias_n = max(bias_counts) if bias_counts else 0
    bias_available = min(1.0, best_bias_n / 30.0)

    csgd_success = 1.0 if csgd_result.method == "csgd" else 0.3

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