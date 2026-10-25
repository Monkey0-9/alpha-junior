"""
nexus/ui/app.py — Nexus Superhuman Brain Dashboard

Full glassmorphic Streamlit matrix with:
  - SuperhumanBrain conviction panel (A++ grade, gate status, IR score)
  - Regime probability distribution bar chart
  - Hurst exponent + market structure insight
  - Strategy Bayesian agreement (17 strategies)
  - Global IC score + meta-learning status
  - Portfolio holdings with real-time P&L
  - Correlation crisis alert
  - Recent orders audit log
"""

import streamlit as st
import pandas as pd
import httpx
import time
from datetime import datetime
import os
from typing import Optional, Any
from streamlit_autorefresh import st_autorefresh

# --- Page Config ---
st.set_page_config(
    page_title="Nexus Superhuman Brain Matrix",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --- Premium CSS ---
st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;900&display=swap');

    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
        background-color: #050505;
        color: #e0e0e0;
    }
    .stApp {
        background: radial-gradient(ellipse at top right, #0d0d1a 0%, #050505 60%);
    }
    .glass-card {
        background: rgba(255,255,255,0.03);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px;
        padding: 22px 26px;
        margin-bottom: 16px;
        transition: border-color 0.3s ease;
    }
    .glass-card:hover { border-color: rgba(0,255,135,0.25); }
    .brain-card {
        background: linear-gradient(135deg, rgba(0,255,135,0.05) 0%, rgba(0,170,255,0.05) 100%);
        border: 1px solid rgba(0,255,135,0.15);
        border-radius: 18px;
        padding: 22px 26px;
        margin-bottom: 16px;
    }
    .conviction-card {
        background: linear-gradient(135deg, rgba(100,0,255,0.08) 0%, rgba(0,100,255,0.08) 100%);
        border: 1px solid rgba(120,80,255,0.25);
        border-radius: 18px;
        padding: 20px 24px;
        margin-bottom: 14px;
    }
    .alert-crisis {
        background: rgba(255,50,50,0.08);
        border: 1px solid rgba(255,50,50,0.4);
        border-radius: 14px;
        padding: 16px 22px;
        margin-bottom: 16px;
        animation: pulse-red 2s infinite;
    }
    @keyframes pulse-red {
        0%,100% { border-color: rgba(255,50,50,0.4); }
        50%  { border-color: rgba(255,50,50,0.9); }
    }
    .metric-label {
        color: #666;
        font-size: 0.78rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1.2px;
        margin-bottom: 4px;
    }
    .metric-value {
        color: #fff;
        font-size: 1.9rem;
        font-weight: 700;
        line-height: 1.1;
    }
    .metric-sub {
        color: #888;
        font-size: 0.8rem;
        margin-top: 4px;
    }
    .grade-axx { color: #00ff87; font-weight: 900; font-size: 1.6rem; }
    .grade-ax  { color: #40e0a0; font-weight: 800; font-size: 1.5rem; }
    .grade-a   { color: #80d0b0; font-weight: 700; font-size: 1.4rem; }
    .grade-bx  { color: #ffd60a; font-weight: 700; font-size: 1.4rem; }
    .grade-b   { color: #ffaa00; font-weight: 600; font-size: 1.3rem; }
    .grade-c   { color: #ff6655; font-weight: 600; font-size: 1.3rem; }
    .status-active {
        padding: 4px 14px; border-radius: 20px;
        font-size: 0.73rem; font-weight: 700;
        text-transform: uppercase;
        background: rgba(0,255,135,0.1);
        color: #00ff87; border: 1px solid rgba(0,255,135,0.5);
    }
    .regime-bull       { color: #00ff87; }
    .regime-bear       { color: #ff4b4b; }
    .regime-sideways   { color: #ffd60a; }
    .regime-turbulent  { color: #ff8c00; }
    .gate-open    { color: #00ff87; }
    .gate-partial { color: #ffd60a; }
    .gate-closed  { color: #ff4b4b; }
    h1,h2,h3 { color: #fff !important; font-weight: 700 !important; }
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 8px; }
    .stDataFrame { border-radius: 12px; overflow: hidden; }
    .section-divider {
        border: none;
        border-top: 1px solid rgba(255,255,255,0.06);
        margin: 28px 0 20px 0;
    }
</style>
""",
    unsafe_allow_html=True,
)

BACKEND_URL = os.getenv("NEXUS_BACKEND_URL", "http://localhost:8001")
API_KEY = os.getenv("NEXUS_API_KEY", "")


def fetch(endpoint: str) -> Optional[Any]:
    """Synchronous HTTP fetch with API Key authentication."""
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        with httpx.Client(timeout=12, headers=headers) as client:
            resp = client.get(f"{BACKEND_URL}/{endpoint}")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return None


def grade_css(grade: str) -> str:
    """Map conviction grade to CSS class."""
    return {
        "A++": "grade-axx",
        "A+": "grade-ax",
        "A": "grade-a",
        "B+": "grade-bx",
        "B": "grade-b",
        "C": "grade-c",
    }.get(grade, "grade-c")


def regime_color(regime: str) -> str:
    return {
        "BULL": "#00ff87",
        "BEAR": "#ff4b4b",
        "SIDEWAYS": "#ffd60a",
        "TURBULENT": "#ff8c00",
    }.get(regime.upper(), "#aaa")


def hurst_label(h: float) -> tuple[str, str]:
    """Returns (label, color) for Hurst exponent."""
    if h > 0.60:
        return "TRENDING ↗", "#00ff87"
    if h < 0.40:
        return "MEAN-REVERTING ↔", "#00aaff"
    return "RANDOM WALK ~", "#ffd60a"


def main() -> None:
    # ── Header ─────────────────────────────────────────────────────────────
    col_h1, col_h2 = st.columns([3, 1])
    with col_h1:
        st.markdown(
            "# 🧠 NEXUS <span style='color:#00ff87'>SUPERHUMAN BRAIN</span>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p style='color:#555;margin-top:-12px;font-size:0.9rem;'>"
            "Institutional Edition v3.0 · Bayesian · Kelly · Fractal · Meta-Learning · 24/7 Autonomous"
            "</p>",
            unsafe_allow_html=True,
        )
    with col_h2:
        st.markdown(
            "<div style='text-align:right;margin-top:22px;'>"
            "<span class='status-active'>⚡ BRAIN ONLINE</span></div>",
            unsafe_allow_html=True,
        )

    # ── Auto-Refresh ────────────────────────────────────────────────────────
    # Refresh every 30 seconds (30000 milliseconds) for paper trading
    # responsiveness
    st_autorefresh(interval=30000, limit=None, key="dashboard_autorefresh")

    # ── Account Metrics ─────────────────────────────────────────────────────
    data_account = fetch("api/alpaca/account")
    if data_account:
        equity = float(data_account.get("portfolio_value", 0))
        buying_power = float(data_account.get("buying_power", 0))
        cash = float(data_account.get("cash", 0))
        mode = "SIMULATED" if data_account.get("simulated") else "LIVE PAPER"
        mode_color = "#00ff87" if mode == "LIVE PAPER" else "#ffd60a"

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(
                f"<div class='glass-card'><div class='metric-label'>Portfolio Equity</div>"
                f"<div class='metric-value'>${equity:,.2f}</div></div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"<div class='glass-card'><div class='metric-label'>Buying Power</div>"
                f"<div class='metric-value'>${buying_power:,.2f}</div></div>",
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                f"<div class='glass-card'><div class='metric-label'>Cash Balance</div>"
                f"<div class='metric-value'>${cash:,.2f}</div></div>",
                unsafe_allow_html=True,
            )
        with c4:
            st.markdown(
                f"<div class='glass-card'><div class='metric-label'>Mode</div>"
                f"<div class='metric-value' style='color:{mode_color}'>{mode}</div></div>",
                unsafe_allow_html=True,
            )
    else:
        st.warning("⏳ Waiting for backend connection...")

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # ── SuperhumanBrain Conviction Panel ────────────────────────────────────
    st.markdown("### 🧠 SuperhumanBrain — Live Conviction Signals")
    brain_data = fetch(
        "api/monitor/brain/superhuman?symbols=AAPL,MSFT,NVDA,SPY,QQQ,AMZN,TSLA,GOOG"
    )
    analysis = fetch("api/monitor/brain")
    market_analysis = analysis.get("analysis", {}) if analysis else {}

    if brain_data and brain_data.get("status") == "success":
        intel = brain_data.get("intelligence_report", {})
        signals = brain_data.get("signals", {})
        regime_probs = brain_data.get("regime_probs", {})
        current_regime = brain_data.get("regime", "UNKNOWN")

        # ── Intelligence Summary Row ────────────────────────────────────────
        s1, s2, s3, s4, s5 = st.columns(5)
        with s1:
            avg_conv = intel.get("avg_conviction", 0)
            st.markdown(
                f"<div class='brain-card'><div class='metric-label'>Avg Conviction</div>"
                f"<div class='metric-value' style='color:#00ff87'>{avg_conv:.0%}</div>"
                f"<div class='metric-sub'>Portfolio-level confidence</div></div>",
                unsafe_allow_html=True,
            )
        with s2:
            global_ic = intel.get("global_ic", 0)
            ic_color = (
                "#00ff87"
                if global_ic > 0.2
                else "#ffd60a" if global_ic > 0 else "#ff4b4b"
            )
            st.markdown(
                f"<div class='brain-card'><div class='metric-label'>Global IC Score</div>"
                f"<div class='metric-value' style='color:{ic_color}'>{global_ic:.3f}</div>"
                f"<div class='metric-sub'>Meta-learning calibration</div></div>",
                unsafe_allow_html=True,
            )
        with s3:
            a_grade = intel.get("a_grade_signals", 0)
            total = intel.get("total_signals", 1)
            st.markdown(
                f"<div class='brain-card'><div class='metric-label'>A-Grade Signals</div>"
                f"<div class='metric-value' style='color:#7b61ff'>{a_grade}/{total}</div>"
                f"<div class='metric-sub'>Conviction ≥ 65%</div></div>",
                unsafe_allow_html=True,
            )
        with s4:
            gate_rate = intel.get("gate_pass_rate", 0)
            gate_color = "#00ff87" if gate_rate > 0.7 else "#ffd60a"
            st.markdown(
                f"<div class='brain-card'><div class='metric-label'>Fractal Gate Rate</div>"
                f"<div class='metric-value' style='color:{gate_color}'>{gate_rate:.0%}</div>"
                f"<div class='metric-sub'>Signals passing fractal filter</div></div>",
                unsafe_allow_html=True,
            )
        with s5:
            rc = regime_color(current_regime)
            st.markdown(
                f"<div class='brain-card'><div class='metric-label'>Active Regime</div>"
                f"<div class='metric-value' style='color:{rc}'>{current_regime}</div>"
                f"<div class='metric-sub'>Dominant market state</div></div>",
                unsafe_allow_html=True,
            )

        # ── Regime Probability Distribution ────────────────────────────────
        if regime_probs:
            st.markdown("#### Regime Probability Distribution")
            rp_df = pd.DataFrame(
                [
                    {"Regime": k, "Probability": round(v * 100, 1)}
                    for k, v in sorted(regime_probs.items(), key=lambda x: -x[1])
                ]
            )
            bar_col, hurst_col = st.columns([2, 1])
            with bar_col:
                st.bar_chart(
                    rp_df.set_index("Regime")["Probability"],
                    color="#00ff87",
                    width="stretch",
                    height=160,
                )
            with hurst_col:
                hurst = market_analysis.get("hurst_exponent", 0.5)
                h_label, h_color = hurst_label(hurst)
                forecast_conf = market_analysis.get("forecast_confidence", 0)
                st.markdown(
                    f"<div class='glass-card'>"
                    f"<div class='metric-label'>Hurst Exponent (H)</div>"
                    f"<div class='metric-value' style='color:{h_color}'>{hurst:.3f}</div>"
                    f"<div style='color:{h_color};font-size:0.85rem;font-weight:600;margin-top:4px'>{h_label}</div>"
                    f"<div class='metric-sub' style='margin-top:10px'>Forecast Confidence</div>"
                    f"<div style='color:#aaa;font-size:1.2rem;font-weight:600'>{forecast_conf:.0%}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        # ── Correlation Crisis Alert ────────────────────────────────────────
        corr_pulse = market_analysis.get("correlation_pulse", {})
        if isinstance(corr_pulse, dict) and corr_pulse.get("crisis_mode"):
            st.markdown(
                "<div class='alert-crisis'>"
                "⚠️ <strong>CORRELATION CRISIS DETECTED</strong> — All positions moving together. "
                f"Correlation proxy: {corr_pulse.get('avg_correlation', 0):.2f} · "
                "Position sizes automatically halved by SuperhumanBrain."
                "</div>",
                unsafe_allow_html=True,
            )

        # ── Per-Symbol Conviction Cards ─────────────────────────────────────
        st.markdown("#### Signal Conviction — Per Symbol")
        sorted_signals = sorted(
            signals.items(),
            key=lambda x: x[1].get("conviction", 0),
            reverse=True,
        )

        # 4 cards per row
        for row_start in range(0, len(sorted_signals), 4):
            row = sorted_signals[row_start : row_start + 4]
            cols = st.columns(4)
            for col, (sym, sig) in zip(cols, row):
                grade = sig.get("grade", "C")
                conv = sig.get("conviction_pct", "0%")
                score = sig.get("score", 0.0)
                ir = sig.get("ir_score", 0.0)
                gate = sig.get("gate_pass", False)
                bias = sig.get("regime_bias", "?")
                g_class = grade_css(grade)

                score_color = (
                    "#00ff87"
                    if score > 0.1
                    else "#ff4b4b" if score < -0.1 else "#ffd60a"
                )
                gate_text = (
                    "<span class='gate-open'>● OPEN</span>"
                    if gate
                    else "<span class='gate-closed'>● CLOSED</span>"
                )
                b_color = regime_color(bias)

                with col:
                    st.markdown(
                        f"<div class='conviction-card'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                        f"<div style='font-size:1.1rem;font-weight:700;color:#fff'>{sym}</div>"
                        f"<div class='{g_class}'>{grade}</div>"
                        f"</div>"
                        f"<div style='color:#888;font-size:0.72rem;margin:4px 0 10px 0'>"
                        f"Fractal Gate: {gate_text}</div>"
                        f"<div class='metric-label'>Conviction</div>"
                        f"<div style='color:#00aaff;font-size:1.4rem;font-weight:700'>{conv}</div>"
                        f"<div style='display:flex;gap:16px;margin-top:10px'>"
                        f"<div><div class='metric-label'>Signal</div>"
                        f"<div style='color:{score_color};font-weight:700'>{score:+.3f}</div></div>"
                        f"<div><div class='metric-label'>IR</div>"
                        f"<div style='color:#aaa;font-weight:600'>{ir:.3f}</div></div>"
                        f"<div><div class='metric-label'>Bias</div>"
                        f"<div style='color:{b_color};font-weight:600;font-size:0.8rem'>{bias}</div></div>"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
    else:
        st.info("🧠 SuperhumanBrain data loading... Backend may be starting up.")

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # ── Market Intelligence (Classic Brain) ─────────────────────────────────
    st.markdown("### 📡 Market Intelligence Matrix")
    i1, i2, i3 = st.columns(3)

    with i1:
        regime = market_analysis.get("regime", "UNCERTAIN")
        forecast = market_analysis.get("market_forecast", "NEUTRAL")
        strategy = market_analysis.get("selected_strategy", "N/A")
        rc = regime_color(regime)
        st.markdown(
            f"<div class='glass-card'>"
            f"<div class='metric-label'>Market Regime & Forecast</div>"
            f"<div style='color:{rc};font-size:1.6rem;font-weight:700'>{regime}</div>"
            f"<div style='color:#888;font-size:0.9rem;margin-top:4px'>{forecast}</div>"
            f"<div class='metric-sub' style='margin-top:8px'>Active Strategy: {strategy}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with i2:
        agreement = market_analysis.get("strategy_agreement", 0.5)
        st.markdown(
            f"<div class='glass-card'>"
            f"<div class='metric-label'>Bayesian Strategy Agreement</div>"
            f"<div class='metric-value' style='color:#00aaff'>{agreement:.0%}</div>"
            f"<div class='metric-sub'>Consensus across 17 quant strategies</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.progress(float(agreement))
    with i3:
        sentiment = market_analysis.get("market_sentiment", 0.5)
        s_label = market_analysis.get("market_sentiment_label", "Neutral")
        s_color = (
            "#00ff87"
            if sentiment > 0.55
            else "#ff4b4b" if sentiment < 0.45 else "#ffd60a"
        )
        st.markdown(
            f"<div class='glass-card'>"
            f"<div class='metric-label'>Quantitative Sentiment</div>"
            f"<div class='metric-value' style='color:{s_color}'>{sentiment:.3f}</div>"
            f"<div style='color:{s_color};font-size:0.85rem;font-weight:600'>{s_label}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # ── Holdings Table ──────────────────────────────────────────────────────
    st.markdown("### 📊 Institutional Holdings")
    positions_data = fetch("api/alpaca/positions")
    pos_list = positions_data.get("positions", []) if positions_data else []

    if pos_list:
        pos_df = pd.DataFrame(pos_list)
        display_cols = [
            "symbol",
            "qty",
            "avg_price",
            "current_price",
            "market_value",
            "unrealized_plpc",
            "side",
        ]
        for display_col in display_cols:
            if display_col not in pos_df.columns:
                pos_df[display_col] = 0.0
        display = pos_df[display_cols].copy()
        display.columns = [
            "Symbol",
            "Shares",
            "Avg Price",
            "Mkt Price",
            "Value",
            "P&L %",
            "Side",
        ]
        st.dataframe(
            display.style.format(
                {
                    "Avg Price": "${:,.2f}",
                    "Mkt Price": "${:,.2f}",
                    "Value": "${:,.2f}",
                    "P&L %": "{:+.2f}%",
                }
            ),
            width="stretch",
            height=280,
        )
    else:
        st.info("No active holdings.")

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # ── Recent Orders ───────────────────────────────────────────────────────
    st.markdown("### 📋 Recent Orders — Audit Log")
    orders_data = fetch("api/alpaca/orders?status=all&limit=10")
    if orders_data:
        order_list = orders_data.get("orders", [])
        if order_list:
            odf = pd.DataFrame(order_list)
            show_cols = [
                c
                for c in [
                    "symbol",
                    "side",
                    "qty",
                    "type",
                    "status",
                    "strategy",
                ]
                if c in odf.columns
            ]
            st.dataframe(odf[show_cols], width="stretch", height=220)
        else:
            st.info("No recent orders.")

    # ── Footer ──────────────────────────────────────────────────────────────
    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)
    fc1, fc2, fc3 = st.columns([2, 1, 1])
    with fc1:
        st.caption(
            f"🧠 SuperhumanBrain v3.0 · Last refresh: {datetime.now().strftime('%H:%M:%S')}"
        )
    with fc2:
        st.caption("Bayesian · Kelly · Fractal · IC · 17 Strategies")
    with fc3:
        if st.button("⟳ Force Refresh", key="refresh_btn"):
            st.rerun()

    time.sleep(30)
    st.rerun()


if __name__ == "__main__":
    main()
