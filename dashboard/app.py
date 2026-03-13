"""Weather Edge Live Dashboard — Streamlit app for monitoring Kalshi weather signals.

Run: cd ~/Projects/polymarket-weather-bot && streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timezone

from kalshi.scanner import get_kalshi_weather_markets, get_kalshi_precip_markets, parse_kalshi_bucket, get_kalshi_price
from kalshi.trader import get_balance, get_positions, get_orders
from weather.multi_model import fuse_forecast, fuse_precip_forecast
from weather.forecast import calculate_remaining_month_days
from config import (
    PAPER_MODE, MAX_ENSEMBLE_HORIZON_DAYS, ALERT_THRESHOLD, CONFIDENCE_THRESHOLD,
    MAX_ORDER_USD, MAX_SCAN_BUDGET, KELLY_MAX_FRACTION, DRAWDOWN_THRESHOLD,
    FRACTIONAL_KELLY, DAILY_STOP_PCT,
)

st.set_page_config(page_title="Weather Edge Live", layout="wide")
st.title("Weather Edge — Live Trading Monitor")

mode_label = "PAPER" if PAPER_MODE else "LIVE"
mode_color = "orange" if PAPER_MODE else "green"
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')} | Mode: :{mode_color}[**{mode_label}**]")

if st.button("Refresh"):
    st.rerun()

# ---------------------------------------------------------------------------
# HOW IT WORKS
# ---------------------------------------------------------------------------
with st.expander("How This Works", expanded=False):
    st.markdown("""
**What is this?** A bot that finds mispriced weather contracts on Kalshi and trades them automatically.

**The pipeline:**
1. **Scan** ~214 Kalshi weather markets (temperature + precipitation) every 15 minutes
2. **Forecast** each market using 3 independent weather models (Open-Meteo Ensemble, NOAA NWS, GFS+HRRR), bias-corrected with historical actuals
3. **Detect edge** — if our model probability differs from the market price by 7%+, that's a tradeable signal
4. **Score confidence** (55-100) — based on model agreement, ensemble spread, bias data quality, and data availability
5. **Size positions** using sigmoid-scaled Kelly criterion (0.25x-0.50x Kelly based on confidence + edge strength)
6. **Filter for fees** — skip any trade where expected profit after Kalshi fees < $0.12
7. **Execute strongest first** — signals sorted by `|edge| * confidence`, best opportunities get budget priority

**Reading the tables below:**
- **Model** = our forecast probability that YES wins
- **Market** = Kalshi's current YES price (what the market thinks)
- **Edge** = Model - Market. Green/positive = we think YES is underpriced. Red/negative = we think YES is overpriced (buy NO)
- **Confidence** = 55-100 score. Higher = models agree more, tighter ensemble, better bias data
- **Direction** = BUY YES (we think it will happen) or SELL YES (we think it won't — equivalent to buying NO)

**Risk controls:** {KELLY_MAX_FRACTION:.0%} max bankroll per order | ${MAX_ORDER_USD:.0f} per order | ${MAX_SCAN_BUDGET:.0f} per scan | {DRAWDOWN_THRESHOLD:.0%} drawdown circuit breaker | {DAILY_STOP_PCT:.0%} daily stop
""")

# ---------------------------------------------------------------------------
# PORTFOLIO OVERVIEW
# ---------------------------------------------------------------------------
st.header("Portfolio")

try:
    bal = get_balance()
    cash = bal.get("balance", 0) / 100.0
    portfolio_val = bal.get("portfolio_value", 0) / 100.0
    total_equity = cash + portfolio_val
    deployed_pct = portfolio_val / total_equity * 100 if total_equity > 0 else 0

    col_bal1, col_bal2, col_bal3, col_bal4 = st.columns(4)
    col_bal1.metric("Cash", f"${cash:.2f}")
    col_bal2.metric("Positions", f"${portfolio_val:.2f}")
    col_bal3.metric("Total Equity", f"${total_equity:.2f}")
    col_bal4.metric("Deployed", f"{deployed_pct:.1f}%")

    # --- Settled P&L ---
    settled_positions = get_positions(limit=200, settlement_status="settled")
    if settled_positions:
        settled_pnl = sum(float(p.get("realized_pnl_dollars", "0")) for p in settled_positions)
        settled_fees = sum(float(p.get("fees_paid_dollars", "0")) for p in settled_positions)
        settled_net = settled_pnl - settled_fees
        wins = sum(1 for p in settled_positions if float(p.get("realized_pnl_dollars", "0")) > 0)
        losses = sum(1 for p in settled_positions if float(p.get("realized_pnl_dollars", "0")) < 0)
        hit_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

        col_s1, col_s2, col_s3, col_s4 = st.columns(4)
        col_s1.metric("Settled Gross P&L", f"${settled_pnl:+.2f}")
        col_s2.metric("Fees Paid", f"${settled_fees:.2f}")
        col_s3.metric("Net P&L", f"${settled_net:+.2f}", delta=f"{settled_net:+.2f}")
        col_s4.metric("Hit Rate", f"{hit_rate:.0f}% ({wins}W/{losses}L)")

    # --- Open Positions ---
    all_positions = get_positions()
    open_positions = [p for p in all_positions if float(p.get("position_fp", "0")) != 0]

    if open_positions:
        st.subheader(f"Open Positions ({len(open_positions)})")
        st.caption("Each row is a contract you hold. **Side** = YES or NO. **Entry** = average price paid per contract. "
                   "**Exposure** = what you'd receive if the contract settles in your favor. "
                   "**P&L** = unrealized profit/loss (green = winning, red = losing).")
        pos_rows = []
        total_cost = 0.0
        total_current_val = 0.0
        total_fees = 0.0
        for p in open_positions:
            qty = float(p.get("position_fp", "0"))
            abs_qty = abs(qty)
            side = "YES" if qty > 0 else "NO"
            cost = float(p.get("total_traded_dollars", "0"))
            exposure = float(p.get("market_exposure_dollars", "0"))
            realized = float(p.get("realized_pnl_dollars", "0"))
            fees = float(p.get("fees_paid_dollars", "0"))

            # Entry price per contract
            entry_price = cost / abs_qty if abs_qty > 0 else 0

            # Current value = exposure (what we'd get if it settled in our favor)
            # Unrealized P&L = exposure - cost + realized
            unrealized = exposure - cost + realized

            total_cost += cost
            total_current_val += exposure
            total_fees += fees

            pos_rows.append({
                "Ticker": p.get("ticker", ""),
                "Side": side,
                "Qty": int(abs_qty),
                "Entry": f"${entry_price:.2f}",
                "Exposure": f"${exposure:.2f}",
                "P&L": unrealized,
                "Fees": f"${fees:.2f}",
            })

        df_pos = pd.DataFrame(pos_rows)

        def highlight_pnl(row):
            pnl = row["P&L"]
            if pnl > 0:
                return ["background-color: #d4edda"] * len(row)
            elif pnl < 0:
                return ["background-color: #f8d7da"] * len(row)
            return [""] * len(row)

        st.dataframe(
            df_pos.style.apply(highlight_pnl, axis=1).format({"P&L": "${:+.2f}"}),
            use_container_width=True,
            height=min(400, 50 + len(pos_rows) * 35),
        )

        total_unrealized = total_current_val - total_cost
        st.caption(
            f"Total cost: ${total_cost:.2f} | Current exposure: ${total_current_val:.2f} | "
            f"Unrealized: ${total_unrealized:+.2f} | Fees: ${total_fees:.2f}"
        )
    else:
        st.info("No open positions")

    # --- Resting Orders ---
    resting = get_orders(status="resting")
    if resting:
        with st.expander(f"Resting Orders ({len(resting)}) — limit orders waiting to fill"):
            order_rows = []
            for o in resting:
                remaining = float(o.get("remaining_count_fp", "0"))
                price = o.get("yes_price_dollars") or o.get("no_price_dollars") or "—"
                order_rows.append({
                    "Ticker": o.get("ticker", ""),
                    "Action": o.get("action", "").upper(),
                    "Side": o.get("side", "").upper(),
                    "Remaining": int(remaining),
                    "Price": price,
                    "Created": o.get("created_time", "")[:16].replace("T", " "),
                })
            st.dataframe(pd.DataFrame(order_rows), use_container_width=True,
                         height=min(400, 50 + len(order_rows) * 35))

except Exception as e:
    st.warning(f"Could not load portfolio data: {e}")

st.divider()


def highlight_edge(row):
    e = row["Edge"]
    if abs(e) >= ALERT_THRESHOLD:
        return ["background-color: #d4edda"] * len(row) if e > 0 else ["background-color: #f8d7da"] * len(row)
    return [""] * len(row)


# ---------------------------------------------------------------------------
# PRECIP MARKETS
# ---------------------------------------------------------------------------
st.header("Precipitation Markets (KXRAIN*)")
st.caption("Monthly cumulative rainfall contracts. **Threshold** = inches of rain needed for YES to win. "
           "These markets settle at month-end based on official station readings.")

with st.spinner("Scanning KXRAIN* markets..."):
    precip_markets = get_kalshi_precip_markets()
    month = datetime.now(timezone.utc).month
    remaining_days = calculate_remaining_month_days()

    precip_rows = []
    for m in precip_markets:
        try:
            yes_price = get_kalshi_price(m)
            if yes_price is None:
                continue

            threshold = m.get("_threshold", 0.0)
            city = m.get("_city", "unknown")
            ticker = m.get("ticker", "")

            # Cap forecast window to ensemble horizon; show all markets but flag reduced accuracy
            forecast_window = min(remaining_days, MAX_ENSEMBLE_HORIZON_DAYS)
            beyond_horizon = remaining_days > MAX_ENSEMBLE_HORIZON_DAYS

            prob, confidence, details = fuse_precip_forecast(
                m["_lat"], m["_lon"], city, month,
                threshold=threshold, forecast_days=forecast_window,
            )

            edge = prob - yes_price
            direction = "BUY YES" if edge > 0 else "SELL YES"
            method = details.get("ensemble", {}).get("method", "?")

            precip_rows.append({
                "City": city.replace("_", " ").title(),
                "Ticker": ticker,
                "Threshold": f">{threshold:.1f} in",
                "Model": f"{prob:.1%}",
                "Market": f"{yes_price:.0%}",
                "Edge": edge,
                "Direction": direction,
                "Confidence": confidence,
                "Method": method.upper(),
                "Days Left": remaining_days,
            })
        except Exception as e:
            st.warning(f"Skipped {m.get('ticker', '?')}: {e}")

if precip_rows:
    df_precip = pd.DataFrame(precip_rows)
    st.dataframe(
        df_precip.style.apply(highlight_edge, axis=1).format({"Edge": "{:+.1%}"}),
        use_container_width=True,
        height=min(400, 50 + len(precip_rows) * 35),
    )

    col1, col2 = st.columns(2)
    with col1:
        fig = px.bar(
            df_precip, x="City", y="Edge", color="Direction",
            title="Precip Edge by City — how much our model disagrees with the market",
            color_discrete_map={"BUY YES": "#28a745", "SELL YES": "#dc3545"},
        )
        fig.update_layout(yaxis_tickformat=".1%", yaxis_title="Edge (Model - Market)")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig2 = px.scatter(
            df_precip, x="Confidence", y="Edge", color="City",
            title="Confidence vs Edge — top-right = strongest signals",
            hover_data=["Ticker", "Threshold"],
        )
        fig2.update_layout(yaxis_tickformat=".1%", xaxis_title="Confidence (55-100)", yaxis_title="Edge")
        st.plotly_chart(fig2, use_container_width=True)

    strong = df_precip[(df_precip["Edge"].abs() >= ALERT_THRESHOLD) & (df_precip["Confidence"] >= CONFIDENCE_THRESHOLD)]
    if not strong.empty:
        st.success(f"Trade-worthy precip signals: {len(strong)} in {', '.join(strong['City'].unique())}")
else:
    st.info("No precip markets with pricing found — check debug below")

# ---------------------------------------------------------------------------
# TEMPERATURE MARKETS
# ---------------------------------------------------------------------------
st.header("Temperature Markets (KXHIGH*)")
st.caption("Daily high temperature contracts across 14 US cities. **Bucket** = temperature range for YES to win. "
           "Markets settle next day based on official airport station readings.")

with st.spinner("Scanning temperature markets..."):
    temp_markets = get_kalshi_weather_markets()

    temp_rows = []
    for m in temp_markets:
        try:
            yes_price = get_kalshi_price(m)
            if yes_price is None:
                continue

            bucket = parse_kalshi_bucket(m)
            if not bucket:
                continue
            low, high = bucket

            city = m.get("_city", "unknown")
            ticker = m.get("ticker", "")

            prob, confidence, details = fuse_forecast(
                m["_lat"], m["_lon"], city, month,
                low, high, days_ahead=1, unit=m.get("_unit", "f"),
            )

            edge = prob - yes_price
            direction = "BUY YES" if edge > 0 else "SELL YES"
            bucket_str = f"{low:.0f}-{high:.0f}" if high else f">{low:.0f}"

            temp_rows.append({
                "City": city.replace("_", " ").title(),
                "Ticker": ticker,
                "Bucket": bucket_str,
                "Model": f"{prob:.1%}",
                "Market": f"{yes_price:.0%}",
                "Edge": edge,
                "Direction": direction,
                "Confidence": confidence,
            })
        except Exception as e:
            st.warning(f"Skipped {m.get('ticker', '?')}: {e}")

if temp_rows:
    df_temp = pd.DataFrame(temp_rows)
    st.dataframe(
        df_temp.style.apply(highlight_edge, axis=1).format({"Edge": "{:+.1%}"}),
        use_container_width=True,
        height=min(400, 50 + len(temp_rows) * 35),
    )

    col3, col4 = st.columns(2)
    with col3:
        fig3 = px.bar(
            df_temp, x="City", y="Edge", color="Direction",
            title="Temp Edge by City — how much our model disagrees with the market",
            color_discrete_map={"BUY YES": "#28a745", "SELL YES": "#dc3545"},
        )
        fig3.update_layout(yaxis_tickformat=".1%", yaxis_title="Edge (Model - Market)")
        st.plotly_chart(fig3, use_container_width=True)
    with col4:
        fig4 = px.scatter(
            df_temp, x="Confidence", y="Edge", color="City",
            title="Confidence vs Edge — top-right = strongest signals",
            hover_data=["Ticker", "Bucket"],
        )
        fig4.update_layout(yaxis_tickformat=".1%", xaxis_title="Confidence (55-100)", yaxis_title="Edge")
        st.plotly_chart(fig4, use_container_width=True)

    strong_temp = df_temp[(df_temp["Edge"].abs() >= ALERT_THRESHOLD) & (df_temp["Confidence"] >= CONFIDENCE_THRESHOLD)]
    if not strong_temp.empty:
        st.success(f"Trade-worthy temp signals: {len(strong_temp)} in {', '.join(strong_temp['City'].unique())}")
else:
    st.info("No temperature markets with pricing found")

# ---------------------------------------------------------------------------
# DEBUG & FOOTER
# ---------------------------------------------------------------------------
with st.expander("Debug: Raw Market Counts"):
    st.write(f"Precip markets from scanner: **{len(precip_markets)}**")
    st.write(f"Temperature markets from scanner: **{len(temp_markets)}**")
    st.write(f"Remaining days in month: **{remaining_days}**")
    st.write(f"Horizon limit: **{MAX_ENSEMBLE_HORIZON_DAYS}** days")
    if precip_markets:
        st.json(precip_markets[0], expanded=False)
    if temp_markets:
        st.json(temp_markets[0], expanded=False)

st.divider()
col_a, col_b, col_c = st.columns(3)
col_a.metric("Precip Signals", len(precip_rows))
col_b.metric("Temp Signals", len(temp_rows))
trade_worthy = len([r for r in precip_rows + temp_rows
                     if abs(r["Edge"]) >= ALERT_THRESHOLD and r["Confidence"] >= CONFIDENCE_THRESHOLD])
col_c.metric("Trade-Worthy", trade_worthy)

st.caption(
    f"Scan interval: 15 min | Entry gate: {ALERT_THRESHOLD:.0%} edge + {CONFIDENCE_THRESHOLD} confidence | "
    f"Kelly: {FRACTIONAL_KELLY}x-0.50x (sigmoid) | Max {KELLY_MAX_FRACTION:.0%}/order | "
    f"${MAX_ORDER_USD:.0f}/order | ${MAX_SCAN_BUDGET:.0f}/scan | "
    f"{DRAWDOWN_THRESHOLD:.0%} drawdown breaker | {DAILY_STOP_PCT:.0%} daily stop"
)
