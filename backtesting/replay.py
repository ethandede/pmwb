#!/usr/bin/env python3
"""Walk-forward historical replay backtest.

Answers: "If the current algorithm (post-2026-04-15 fixes) had been running
for the last N days, what trades would it have placed, and how would those
trades have performed at settlement?"

Data sources:
  1. data/scan_cache.db — historical scored signals (ticker, city, price,
     scan_time). Used as the SOURCE OF TRUTH for which markets existed and
     what the Kalshi prices were at each decision point.
  2. Kalshi /markets API — metadata for each unique ticker
     (strike_type, floor_strike, cap_strike). Needed to reconstruct bucket
     bounds. Batched 50 tickers per call.
  3. customer-historical-forecast-api.open-meteo.com — deterministic GFS,
     NBM, and ECMWF forecasts as issued at the time. GFS is our ensemble-mean
     proxy (ensemble historical data isn't stored by Open-Meteo). NBM is
     the NWS sanity proxy — NBM is literally what NWS forecasts blend
     into, so it's a faithful substitute for api.weather.gov forecasts.
  4. customer-archive-api.open-meteo.com — actual observed temperatures
     for settlement evaluation.

Methodology caveats (read these):
  - The bot's production path uses a 30-member ensemble (GFS GEFS). This
    backtest uses deterministic GFS + Normal-CDF bucket probability with
    σ=4.5°F. Our own multi_model.py uses exactly this approach for
    deterministic sources, so it's a faithful proxy, but it's NOT the
    exact same math as the 30-member member-counting used in production.
  - The NWS sanity check in production hits api.weather.gov at live time.
    Here we substitute the historical NBM forecast. NBM is the basis of
    NWS's gridded products, so disagreements should match ≥90% of the
    time, but there may be edge cases where NWS and NBM differ.
  - filter_signals() has stateful filters (held_positions, resting_tickers,
    cross_contract) that we feed empty. This slightly OVERCOUNTS
    hypothetical trades vs a true walk-forward. V2 improvement.
  - The `adjacent_bucket` anti-straddle filter is also skipped for the
    same reason.

Usage:
    python -m backtesting.replay --start 2026-03-27 --end 2026-04-14
    python -m backtesting.replay --start 2026-03-27 --end 2026-04-14 --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENMETEO_API_KEY", "").strip()
if not API_KEY:
    print("WARN: OPENMETEO_API_KEY not set — will fall back to public endpoints (rate limited).", file=sys.stderr)

HIST_FORECAST_URL = "https://customer-historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://customer-archive-api.open-meteo.com/v1/archive"
KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"

# City lookup: short-code used in Kalshi tickers → (lat, lon, unit, scan_cache_city, acis_sid)
# Coords and ACIS station IDs matched to Kalshi's actual settlement stations
# (NWS Climatological Report Daily), verified 2026-04-16.
# Key mismatches found: NYC = Central Park (not LGA), CHI = Midway (not ORD).
CITY_INFO = {
    # Ticker code  : (lat,    lon,     unit, scan_cache_city, acis_station_id)
    "NY":           (40.7829,  -73.9654, "f",  "nyc",          "USW00094728"),  # Central Park
    "CHI":          (41.7868,  -87.7522, "f",  "chicago",      "USW00014819"),  # Midway
    "MIA":          (25.7617,  -80.1918, "f",  "miami",        "USW00012839"),  # MIA airport
    "AUS":          (30.1900,  -97.6700, "f",  "austin",       "USW00013904"),  # AUS airport
    "LAX":          (33.9425, -118.4081, "f",  "los_angeles",  "USW00023174"),  # LAX airport
    "SEA":          (47.4502, -122.3088, "f",  "seattle",      "USW00024233"),  # SEA airport
    "HOU":          (29.7600,  -95.3700, "f",  "houston",      "USW00012960"),  # IAH airport
    "SFO":          (37.6200, -122.3700, "f",  "san_francisco","USW00023234"),  # SFO airport
    "ATL":          (33.6407,  -84.4277, "f",  "atlanta",      "USW00013874"),  # ATL airport
    "DC":           (38.8500,  -77.0400, "f",  "washington_dc","USW00013743"),  # DCA airport
    "BOS":          (42.3700,  -71.0100, "f",  "boston",        "USW00014739"),  # BOS airport
    "PHX":          (33.4300, -112.0100, "f",  "phoenix",      "USW00023183"),  # PHX airport
    "SATX":         (29.5300,  -98.4700, "f",  "san_antonio",  "USW00012921"),  # SAT airport
    "LV":           (36.0800, -115.1500, "f",  "las_vegas",    "USW00023169"),  # LAS airport
}

ACIS_URL = "https://data.rcc-acis.org/StnData"

# Filter thresholds — MATCH pipeline/config.py KALSHI_TEMP (post-2026-04-15)
EDGE_GATE = 0.20
SAMEDAY_EDGE = 0.15
CONFIDENCE_GATE = 60
SAMEDAY_CONFIDENCE = 60
MIN_PRICE_CENTS = 7
NWS_SANITY_DIFF_F = 3.0       # ensemble-vs-NBM threshold
NWS_SANITY_PROXIMITY_F = 1.0  # NO-side bucket buffer
DETERMINISTIC_SPREAD_F = 4.5  # σ for Normal-CDF bucket prob


# ---------------------------------------------------------------------------
# HTTP helper with simple retry
# ---------------------------------------------------------------------------

def http_get_json(url, params=None, retries=2):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "polymarket-weather-bot-replay"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    print(f"  HTTP fail after {retries + 1} tries: {last_err} | {url[:120]}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Ticker parsing: extract city code, settlement date, strike suffix
# ---------------------------------------------------------------------------

_TICKER_RE = re.compile(r"^KXHIGH[T]?([A-Z]+)-(\d{2})([A-Z]{3})(\d{2})-([BT])([\d.]+)$")

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_ticker(ticker: str) -> Optional[dict]:
    m = _TICKER_RE.match(ticker)
    if not m:
        return None
    city_code, yr, mon_str, day, bt, strike = m.groups()
    if city_code not in CITY_INFO:
        return None
    mon = _MONTH_MAP.get(mon_str)
    if not mon:
        return None
    try:
        settlement = date(2000 + int(yr), mon, int(day))
    except ValueError:
        return None
    return {
        "city_code": city_code,
        "settlement": settlement,
        "bt": bt,
        "strike": float(strike),
    }


# ---------------------------------------------------------------------------
# Kalshi metadata fetch
# ---------------------------------------------------------------------------

def fetch_kalshi_metadata(tickers: list[str]) -> dict:
    """Batch-fetch Kalshi market metadata (strike_type, floor, cap, status)."""
    results = {}
    for i in range(0, len(tickers), 50):
        batch = tickers[i : i + 50]
        url = f"{KALSHI_URL}?tickers={','.join(batch)}&limit=200"
        data = http_get_json(url)
        if not data:
            continue
        for m in data.get("markets", []):
            t = m.get("ticker")
            if not t:
                continue
            results[t] = {
                "strike_type": m.get("strike_type"),
                "floor_strike": m.get("floor_strike"),
                "cap_strike": m.get("cap_strike"),
                "subtitle": m.get("yes_sub_title") or m.get("subtitle"),
                "status": m.get("status"),
                "result": m.get("result"),
            }
        print(f"  kalshi metadata: {i + len(batch)}/{len(tickers)}")
    return results


# ---------------------------------------------------------------------------
# Historical forecasts (deterministic) + archive observations
# ---------------------------------------------------------------------------

_fcache: dict = {}


def fetch_historical_forecasts(lat: float, lon: float, start: str, end: str) -> Optional[dict]:
    """Fetch deterministic GFS + NBM + ECMWF for a location and date range.

    Returns parsed `daily` dict with per-model temperature_2m_max arrays.
    """
    key = (round(lat, 3), round(lon, 3), start, end)
    if key in _fcache:
        return _fcache[key]
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": "temperature_2m_max,temperature_2m_min",
        "models": "gfs_seamless,ncep_nbm_conus,ecmwf_ifs025",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
    }
    if API_KEY:
        params["apikey"] = API_KEY
    data = http_get_json(HIST_FORECAST_URL, params)
    result = data.get("daily") if data else None
    _fcache[key] = result
    return result


def fetch_archive_max(lat: float, lon: float, target: str) -> Optional[float]:
    """Actual observed max temp from Open-Meteo archive. DEPRECATED for
    settlement evaluation — use fetch_acis_max instead, which queries
    the same NWS cooperative observer network that Kalshi's CLI product
    draws from."""
    key = ("archive", round(lat, 3), round(lon, 3), target)
    if key in _fcache:
        return _fcache[key]
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": target,
        "end_date": target,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
    }
    if API_KEY:
        params["apikey"] = API_KEY
    data = http_get_json(ARCHIVE_URL, params)
    if not data:
        _fcache[key] = None
        return None
    arr = (data.get("daily") or {}).get("temperature_2m_max", [])
    temp = arr[0] if arr else None
    _fcache[key] = temp
    return temp


def fetch_acis_max(acis_sid: str, target: str) -> Optional[float]:
    """Actual observed max temp from ACIS (NWS cooperative observer network).

    This is the authoritative settlement source — ACIS provides the same
    data that the NWS Climatological Report (Daily) uses, which is what
    Kalshi settles against per their contract rules.

    Returns integer °F (ACIS reports whole degrees) or None.
    """
    key = ("acis", acis_sid, target)
    if key in _fcache:
        return _fcache[key]
    try:
        payload = json.dumps({
            "sid": acis_sid,
            "sdate": target,
            "edate": target,
            "elems": [{"name": "maxt"}],
        }).encode()
        req = urllib.request.Request(
            ACIS_URL,
            data=payload,
            headers={"Content-Type": "application/json",
                     "User-Agent": "polymarket-weather-bot-replay"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        data = d.get("data", [[]])
        if data and len(data[0]) >= 2:
            val = data[0][1]
            if val in ("M", "T", "", None):
                _fcache[key] = None
                return None
            temp = float(val)
            _fcache[key] = temp
            return temp
    except Exception as e:
        print(f"  ACIS fetch error ({acis_sid}, {target}): {e}", file=sys.stderr)
    _fcache[key] = None
    return None


# ---------------------------------------------------------------------------
# Bucket math: Normal-CDF probability of point forecast landing in bucket
# ---------------------------------------------------------------------------

def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def deterministic_bucket_prob(
    forecast: Optional[float],
    strike_type: str,
    floor_strike: Optional[float],
    cap_strike: Optional[float],
    sigma: float = DETERMINISTIC_SPREAD_F,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (model_prob, low, high) for a contract given a point forecast.

    Matches the bucket shapes produced by kalshi.scanner.parse_kalshi_bucket
    (post-Bug #1 fix):
      between:  (floor,    cap)      → [floor, cap)
      greater:  (floor,    None)     → P(temp >= floor)
      less:     (0,        cap)      → P(temp <  cap)
    """
    if forecast is None:
        return None, None, None

    if strike_type == "between" and floor_strike is not None and cap_strike is not None:
        low, high = float(floor_strike), float(cap_strike)
        z_hi = (high - forecast) / sigma
        z_lo = (low - forecast) / sigma
        return phi(z_hi) - phi(z_lo), low, high
    elif strike_type == "greater" and floor_strike is not None:
        low = float(floor_strike)
        z = (low - forecast) / sigma
        return 1 - phi(z), low, None
    elif strike_type == "less" and cap_strike is not None:
        high = float(cap_strike)
        z = (high - forecast) / sigma
        return phi(z), 0.0, high
    return None, None, None


# ---------------------------------------------------------------------------
# Filter stack: mirrors pipeline/stages.py filter_signals + pipeline/config.py
# nws_deterministic_sanity. Kept in-file for isolation from live code paths.
# ---------------------------------------------------------------------------

@dataclass
class HypotheticalSignal:
    ticker: str
    city: str
    city_code: str
    settlement: date
    scan_time: datetime
    days_ahead: int
    side: str                # "yes" or "no"
    model_prob: float
    market_prob: float
    edge: float              # signed
    confidence: int
    price_cents: int
    strike_type: str
    floor_strike: Optional[float]
    cap_strike: Optional[float]
    low: float
    high: Optional[float]
    bot_mean_temp: float     # gfs_seamless historical
    nbm_temp: Optional[float]  # NWS sanity proxy


def _confidence_from_bucket(model_prob: float) -> int:
    """Heuristic: map a bucket probability to a confidence score on 50-95.

    Wider bucket spreads → less confident. A concentrated bucket (prob far
    from 0.5) is high confidence. Matches the flavor of get_ensemble_signal:
    confidence = max(above, total-above)/total * 100, clamped [50, 95].
    """
    # For a single-point deterministic model, "confidence" is ambiguous.
    # Use distance from 0.5 as a proxy.
    c = 50 + (abs(model_prob - 0.5) * 90)  # 0.5 → 50, 1.0 → 95
    return max(50, min(95, round(c)))


def filter_signal(sig: HypotheticalSignal) -> tuple[bool, str]:
    """Return (passes, reason_if_not). Mirrors the current filter_signals."""
    # Same-day overrides
    if sig.days_ahead == 0:
        edge_gate = SAMEDAY_EDGE
        conf_gate = SAMEDAY_CONFIDENCE
    else:
        edge_gate = EDGE_GATE
        conf_gate = CONFIDENCE_GATE

    if abs(sig.edge) < edge_gate:
        return False, "edge_too_low"

    if sig.confidence < conf_gate:
        return False, "confidence_too_low"

    # Reward-to-risk: cost > 50¢ loses
    our_cost_cents = sig.price_cents
    if sig.side == "no":
        our_cost_cents = 100 - sig.price_cents
    if our_cost_cents > 50:
        return False, "reward_risk_violation"
    if our_cost_cents <= MIN_PRICE_CENTS:
        return False, "penny_bet"

    # Forecast-proximity gate: NO bets on buckets the model gives ≥25%
    if sig.side == "no" and sig.model_prob > 0.25:
        return False, "forecast_proximity"

    return True, "passed_base_filters"


def nws_sanity(sig: HypotheticalSignal) -> tuple[bool, str]:
    """Mirrors pipeline/config.py nws_deterministic_sanity but using NBM
    historical as the NWS proxy."""
    if sig.nbm_temp is None:
        return True, "nbm_unavailable"

    # Rule 1: ensemble-vs-NBM disagreement
    diff = abs(sig.bot_mean_temp - sig.nbm_temp)
    if diff > NWS_SANITY_DIFF_F:
        return False, "sanity_ensemble_vs_nbm"

    # Rule 2: bucket proximity
    if sig.high is not None:
        # Between bucket
        if sig.side == "no":
            if (sig.low - NWS_SANITY_PROXIMITY_F) <= sig.nbm_temp <= (sig.high + NWS_SANITY_PROXIMITY_F):
                return False, "sanity_no_near_bucket"
        elif sig.side == "yes":
            if sig.nbm_temp < sig.low - 3 or sig.nbm_temp > sig.high + 3:
                return False, "sanity_yes_far_from_bucket"
    else:
        # Greater-type (YES wins if temp >= low)
        if sig.side == "yes" and sig.nbm_temp < sig.low - 2:
            return False, "sanity_yes_below_floor"
        if sig.side == "no" and sig.nbm_temp > sig.low + 1:
            return False, "sanity_no_above_floor"

    return True, "passed_sanity"


# ---------------------------------------------------------------------------
# Settlement: did the ticker WIN given the actual high?
# ---------------------------------------------------------------------------

def evaluate_settlement(sig: HypotheticalSignal, actual_high: float) -> tuple[bool, int, float]:
    """Return (won, price_won_at_100ths, pnl_dollars)."""
    if sig.strike_type == "between":
        # YES wins if floor <= actual <= cap (1°F inclusive bucket)
        # Kalshi rounds observations to nearest whole degree. We approximate
        # by rounding actual_high and checking inclusive containment.
        actual_round = round(actual_high)
        yes_wins = sig.low <= actual_round <= sig.high  # type: ignore[operator]
    elif sig.strike_type == "greater":
        # YES wins if temp > floor_strike (strictly — "91 or above" with
        # floor_strike=90 means strictly greater)
        yes_wins = actual_high > sig.low
    elif sig.strike_type == "less":
        # YES wins if temp <= cap - 1 (e.g. "83 or below" with cap=84)
        yes_wins = actual_high < sig.high  # type: ignore[operator]
    else:
        return False, 0, 0.0

    i_won = (sig.side == "yes" and yes_wins) or (sig.side == "no" and not yes_wins)
    if i_won:
        pnl = (100 - sig.price_cents) / 100.0
    else:
        pnl = -sig.price_cents / 100.0
    return i_won, sig.price_cents, pnl


# ---------------------------------------------------------------------------
# Main replay
# ---------------------------------------------------------------------------

def load_decisions(db_path: str, start: str, end: str) -> list[dict]:
    """Load scan_cache rows for temp markets in the date range.

    Dedupes to ONE decision per (ticker, scan_date). This prevents
    over-counting — the bot scores the same contract every 5 min, but for
    the backtest one representative scan per day is plenty.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT scan_time, ticker, city, model_prob, market_price, edge, direction
        FROM scan_results
        WHERE market_type = 'temp'
          AND scan_time BETWEEN ? AND ?
          AND ticker LIKE 'KX%'
        ORDER BY scan_time
        """,
        (f"{start}T00:00:00", f"{end}T23:59:59"),
    ).fetchall()
    conn.close()

    # Dedupe: keep the LAST scan per (ticker, scan_date) so we use the
    # freshest market price for each day.
    dedup: dict = {}
    for r in rows:
        scan_time, ticker, city, model_prob, market_price, edge, direction = r
        try:
            st = datetime.fromisoformat(scan_time.replace("Z", "+00:00"))
        except Exception:
            continue
        key = (ticker, st.date())
        dedup[key] = {
            "scan_time": st,
            "ticker": ticker,
            "city": city,
            "old_model_prob": model_prob,
            "market_price": float(market_price) if market_price is not None else None,
            "old_edge": edge,
            "direction": direction,
        }
    return list(dedup.values())


def replay(start: str, end: str, db_path: str, csv_out: Optional[str]) -> None:
    print(f"Walk-forward replay: {start} → {end}")
    print(f"Filters: edge>={EDGE_GATE} (sameday>={SAMEDAY_EDGE}), conf>={CONFIDENCE_GATE}, "
          f"NWS σ={NWS_SANITY_DIFF_F}°F, proximity buffer={NWS_SANITY_PROXIMITY_F}°F")
    print()

    # 1. Load decisions from scan_cache
    decisions = load_decisions(db_path, start, end)
    print(f"Loaded {len(decisions)} deduped decision points from scan_cache.db")

    # 2. Parse tickers, filter to evaluable ones
    parsed = []
    unknown_tickers = Counter()
    for d in decisions:
        p = parse_ticker(d["ticker"])
        if not p:
            unknown_tickers[d["ticker"][:20]] += 1
            continue
        d.update(p)
        parsed.append(d)
    print(f"Parseable tickers: {len(parsed)} / {len(decisions)}")
    if unknown_tickers:
        print(f"  skipped ({sum(unknown_tickers.values())}): {list(unknown_tickers.most_common(5))}")
    print()

    # 3. Only evaluate decisions where settlement has already passed
    today = date.today()
    evaluable = [d for d in parsed if d["settlement"] < today]
    print(f"Settled decisions (settlement < today): {len(evaluable)}")
    print()

    # 4. Fetch Kalshi metadata for unique tickers
    unique_tickers = sorted({d["ticker"] for d in evaluable})
    print(f"Fetching Kalshi metadata for {len(unique_tickers)} unique tickers...")
    metadata = fetch_kalshi_metadata(unique_tickers)
    print(f"  got metadata for {len(metadata)} tickers")
    print()

    # 5. Group decisions by (city_code, settlement) to batch historical forecasts
    by_city_settle: dict = defaultdict(list)
    for d in evaluable:
        by_city_settle[(d["city_code"], d["settlement"])].append(d)
    print(f"Unique (city, settlement-date) groups: {len(by_city_settle)}")
    print()

    # 6. For each unique (city, settlement-date), fetch historical forecasts
    #    and actual outcome. Cache prevents duplicate API calls.
    print("Fetching historical forecasts + actual outcomes...")
    forecasts: dict = {}   # (city_code, settlement) → (gfs, nbm, ecmwf)
    actuals: dict = {}     # (city_code, settlement) → actual high
    for i, (key, ds) in enumerate(by_city_settle.items(), 1):
        city_code, settlement = key
        lat, lon, _unit, _scc, _acis = CITY_INFO[city_code]
        s = settlement.isoformat()
        # Historical forecast: ask for the settlement date itself. Open-Meteo
        # returns the forecast that was valid for that day (issued the day
        # before or morning-of).
        fc = fetch_historical_forecasts(lat, lon, s, s)
        if fc:
            gfs = (fc.get("temperature_2m_max_gfs_seamless") or [None])[0]
            nbm = (fc.get("temperature_2m_max_ncep_nbm_conus") or [None])[0]
            ecmwf = (fc.get("temperature_2m_max_ecmwf_ifs025") or [None])[0]
            forecasts[key] = (gfs, nbm, ecmwf)
        else:
            forecasts[key] = (None, None, None)
        # Use ACIS (NWS cooperative observer) as settlement source — matches
        # Kalshi's NWS Climatological Report (Daily) exactly.
        acis_sid = CITY_INFO[city_code][4] if len(CITY_INFO[city_code]) > 4 else None
        if acis_sid:
            actuals[key] = fetch_acis_max(acis_sid, s)
        else:
            actuals[key] = fetch_archive_max(lat, lon, s)
        if i % 20 == 0:
            print(f"  [{i}/{len(by_city_settle)}]")

    print(f"  forecasts fetched for {sum(1 for v in forecasts.values() if v[0] is not None)}/{len(forecasts)}")
    print(f"  actuals fetched for {sum(1 for v in actuals.values() if v is not None)}/{len(actuals)}")
    print()

    # 7. Evaluate each decision
    results = []
    filter_reasons: Counter = Counter()
    no_metadata = 0
    no_forecast = 0
    no_actual = 0

    for d in evaluable:
        md = metadata.get(d["ticker"])
        if not md or not md.get("strike_type"):
            no_metadata += 1
            continue
        key = (d["city_code"], d["settlement"])
        gfs, nbm, _ecmwf = forecasts.get(key, (None, None, None))
        if gfs is None:
            no_forecast += 1
            continue

        # Compute model_prob from historical GFS deterministic
        model_prob, low, high = deterministic_bucket_prob(
            gfs, md["strike_type"], md["floor_strike"], md["cap_strike"],
        )
        if model_prob is None:
            no_forecast += 1
            continue
        model_prob = max(0.05, min(0.95, model_prob))

        # Market price from scan_cache (we stored YES price as fraction 0-1)
        market_price = d["market_price"]
        if market_price is None:
            continue
        market_prob = float(market_price)
        # scan_cache stores market_price as fraction if <= 1, else cents
        if market_prob > 1.0:
            market_prob /= 100.0

        # Signed edge; side = sign of edge
        edge = model_prob - market_prob
        side = "yes" if edge > 0 else "no"
        confidence = _confidence_from_bucket(model_prob)

        # Price in cents (for YES side); for NO, cost = 100 - yes_price
        price_cents = int(round(market_prob * 100))
        if price_cents < 1:
            price_cents = 1

        days_ahead = max(0, (d["settlement"] - d["scan_time"].date()).days)

        sig = HypotheticalSignal(
            ticker=d["ticker"],
            city=d["city"],
            city_code=d["city_code"],
            settlement=d["settlement"],
            scan_time=d["scan_time"],
            days_ahead=days_ahead,
            side=side,
            model_prob=model_prob,
            market_prob=market_prob,
            edge=edge,
            confidence=confidence,
            price_cents=price_cents,
            strike_type=md["strike_type"],
            floor_strike=md["floor_strike"],
            cap_strike=md["cap_strike"],
            low=low if low is not None else 0.0,
            high=high,
            bot_mean_temp=gfs,
            nbm_temp=nbm,
        )

        # Filter stack
        passes, reason = filter_signal(sig)
        if not passes:
            filter_reasons[reason] += 1
            continue
        passes_sanity, sanity_reason = nws_sanity(sig)
        if not passes_sanity:
            filter_reasons[sanity_reason] += 1
            continue

        # Survived all filters → hypothetical trade. Evaluate settlement.
        actual = actuals.get(key)
        if actual is None:
            no_actual += 1
            continue
        won, _price, pnl = evaluate_settlement(sig, actual)
        results.append({
            "ticker": sig.ticker,
            "scan_day": sig.scan_time.date().isoformat(),
            "settlement": sig.settlement.isoformat(),
            "city_code": sig.city_code,
            "side": sig.side,
            "price_cents": sig.price_cents,
            "model_prob": round(sig.model_prob, 3),
            "market_prob": round(sig.market_prob, 3),
            "edge": round(sig.edge, 3),
            "confidence": sig.confidence,
            "gfs_temp": round(sig.bot_mean_temp, 1),
            "nbm_temp": round(sig.nbm_temp, 1) if sig.nbm_temp is not None else None,
            "actual": round(actual, 1),
            "won": won,
            "pnl": round(pnl, 3),
        })
        filter_reasons["passed_all"] += 1

    # 8. Aggregate and print report
    print("=" * 72)
    print("RESULTS")
    print("=" * 72)
    print()
    print(f"Decisions evaluated:              {len(evaluable)}")
    print(f"  no kalshi metadata:             {no_metadata}")
    print(f"  no historical forecast:         {no_forecast}")
    print(f"  passed all filters → trade:     {len(results)}")
    print(f"  skipped (no actual outcome):    {no_actual}")
    print()
    print("Filter reasons (decisions killed at each stage):")
    for reason, count in filter_reasons.most_common():
        print(f"  {reason:32s} {count:6d}")
    print()

    if not results:
        print("No hypothetical trades survived the filter stack.")
        return

    wins = sum(1 for r in results if r["won"])
    losses = len(results) - wins
    win_rate = wins / len(results) * 100
    total_pnl = sum(r["pnl"] for r in results)
    avg_win = sum(r["pnl"] for r in results if r["won"]) / max(wins, 1)
    avg_loss = sum(r["pnl"] for r in results if not r["won"]) / max(losses, 1)

    print(f"Hypothetical trades: {len(results)}")
    print(f"  Wins:      {wins} ({win_rate:.1f}%)")
    print(f"  Losses:    {losses}")
    print(f"  Avg win:   ${avg_win:+.3f}")
    print(f"  Avg loss:  ${avg_loss:+.3f}")
    print(f"  Net PnL:   ${total_pnl:+.3f}")
    print()

    # By side
    for side_val in ("yes", "no"):
        side_results = [r for r in results if r["side"] == side_val]
        if not side_results:
            continue
        s_wins = sum(1 for r in side_results if r["won"])
        s_pnl = sum(r["pnl"] for r in side_results)
        print(f"  {side_val.upper():3s}: {len(side_results):4d} trades  "
              f"{s_wins}/{len(side_results)} wins ({s_wins/len(side_results)*100:.0f}%)  "
              f"PnL ${s_pnl:+.2f}")

    # By city
    print()
    by_city: dict = defaultdict(list)
    for r in results:
        by_city[r["city_code"]].append(r)
    print("  By city:")
    for cc, rs in sorted(by_city.items()):
        w = sum(1 for r in rs if r["won"])
        p = sum(r["pnl"] for r in rs)
        print(f"    {cc:5s} {len(rs):4d} trades  {w}/{len(rs)} wins ({w/len(rs)*100:.0f}%)  PnL ${p:+.2f}")

    # CSV dump
    if csv_out:
        with open(csv_out, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        print()
        print(f"Wrote {len(results)} rows to {csv_out}")


def main():
    ap = argparse.ArgumentParser(description="Walk-forward replay backtest")
    ap.add_argument("--start", default="2026-03-27", help="inclusive start date (YYYY-MM-DD)")
    ap.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat(),
                    help="inclusive end date (YYYY-MM-DD)")
    ap.add_argument("--db", default="data/scan_cache.db", help="scan_cache.db path")
    ap.add_argument("--csv", default=None, help="optional: write hypothetical trades CSV")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: {args.db} not found", file=sys.stderr)
        sys.exit(1)

    replay(args.start, args.end, args.db, args.csv)


if __name__ == "__main__":
    main()
