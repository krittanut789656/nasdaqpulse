"""
pages/1_Overview.py
===================
NasdaqPulse — Page 1: Market Intelligence Overview
"""

from __future__ import annotations

import math
from typing import List, Dict

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from config.settings import NASDAQ_100_TICKERS, TICKER_INFO
from utils.mongodb_client import load_ticker_metadata, save_ticker_metadata, create_text_index
from utils.yfinance_loader import get_latest_snapshot, fetch_multiple_tickers
from utils.duckdb_engine import (
    get_top_movers, get_sector_summary,
    compute_market_indicators, compute_correlation_matrix, compute_risk_return,
)
from utils.data_quality import generate_quality_report
from utils.logger import log_info, log_error

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Overview · NasdaqPulse",
    page_icon="📊",
    layout="wide",
)

# ── Session state defaults ────────────────────────────────────────────────────
if "selected_ticker" not in st.session_state:
    st.session_state["selected_ticker"] = "NVDA"
if "period" not in st.session_state:
    st.session_state["period"] = "1y"

# ── Header ────────────────────────────────────────────────────────────────────
st.title("NasdaqPulse 📈")
st.caption("Nasdaq 100 Market Intelligence Overview")
st.divider()


# ── Helper: build metadata locally if MongoDB empty ──────────────────────────
def _build_local_metadata() -> pd.DataFrame:
    rows = [{"ticker": t, "name": info["name"], "sector": info["sector"]}
            for t, info in TICKER_INFO.items()]
    return pd.DataFrame(rows)


# ── Cached full OHLCV loader (1y from yfinance, cached 1 h) ──────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _load_all_ohlcv() -> Dict[str, pd.DataFrame]:
    return fetch_multiple_tickers(NASDAQ_100_TICKERS, period="1y")


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

with st.spinner("กำลังโหลดข้อมูลตลาด..."):
    try:
        metadata_df: pd.DataFrame = load_ticker_metadata()
        if metadata_df.empty:
            log_error("MongoDB metadata empty — using local fallback.")
            metadata_df = _build_local_metadata()
            try:
                create_text_index()
                save_ticker_metadata(NASDAQ_100_TICKERS)
            except Exception:
                pass
        for col in ["ticker", "name", "sector"]:
            if col not in metadata_df.columns:
                metadata_df = _build_local_metadata()
                break

        snapshot_df: pd.DataFrame = get_latest_snapshot(NASDAQ_100_TICKERS)
        gainers_df, losers_df = get_top_movers(snapshot_df, n=5)
        sector_df: pd.DataFrame = get_sector_summary(metadata_df, snapshot_df)

        display_df: pd.DataFrame = pd.DataFrame()
        if not snapshot_df.empty and not metadata_df.empty:
            display_df = snapshot_df.merge(
                metadata_df[["ticker", "name", "sector"]], on="ticker", how="left")
            display_df = display_df.rename(columns={
                "ticker": "Ticker", "name": "Company", "sector": "Sector",
                "close": "Price", "change_pct": "Change %", "volume": "Volume",
            })
            display_df = display_df[["Ticker", "Company", "Sector", "Price", "Change %", "Volume"]]

        ohlcv_all: Dict[str, pd.DataFrame] = _load_all_ohlcv()
        indicators_df: pd.DataFrame = compute_market_indicators(ohlcv_all)
        corr_df:       pd.DataFrame = compute_correlation_matrix(ohlcv_all)
        risk_df:       pd.DataFrame = compute_risk_return(ohlcv_all)

        quality_reports: List[dict] = []
        ohlcv_5d = fetch_multiple_tickers(NASDAQ_100_TICKERS, period="5d")
        for ticker_q, df_q in ohlcv_5d.items():
            if not df_q.empty:
                quality_reports.append(generate_quality_report(df_q, ticker_q))

        log_info("Overview page data loaded successfully.")

    except Exception as exc:
        st.error(f"⚠️ Data loading error: {exc}")
        log_error(f"Overview page load failed: {exc}")
        st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 5 — MARKET BREADTH PANEL
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("📡 Market Breadth Panel")

if not indicators_df.empty:
    total = len(indicators_df)
    above_ema20  = int((indicators_df["close"] > indicators_df["ema20"]).sum())
    above_ema50  = int((indicators_df["close"] > indicators_df["ema50"]).sum())
    above_ema200 = int((indicators_df["close"] > indicators_df["ema200"]).sum())
    rsi_ob       = int((indicators_df["rsi"] > 70).sum())
    rsi_os       = int((indicators_df["rsi"] < 30).sum())
    pos_return   = int((indicators_df["return_3m"] > 0).sum())
    neg_return   = total - pos_return

    def _breadth_card(col, label, val, total_n, good_high=True):
        with col:
            pct = val / total_n * 100
            good = (good_high and val >= total_n / 2) or (not good_high and val <= total_n / 2)
            color = "#00d4aa" if good else "#ff4b4b"
            st.markdown(
                f'<div style="background:#161b22;border-radius:8px;padding:14px 10px;text-align:center">'  
                f'<div style="font-size:11px;color:#8b949e;margin-bottom:4px">{label}</div>'  
                f'<div style="font-size:28px;font-weight:700;color:{color}">{val}'  
                f'<span style="font-size:14px;color:#8b949e">/{total_n}</span></div>'  
                f'<div style="font-size:12px;color:#8b949e">{pct:.0f}%</div></div>',
                unsafe_allow_html=True,
            )

    b1, b2, b3, b4, b5, b6, b7 = st.columns(7)
    _breadth_card(b1, "Above EMA20",  above_ema20,  total)
    _breadth_card(b2, "Above EMA50",  above_ema50,  total)
    _breadth_card(b3, "Above EMA200", above_ema200, total)
    _breadth_card(b4, "RSI > 70 🔴",  rsi_ob, total, good_high=False)
    _breadth_card(b5, "RSI < 30 🟢",  rsi_os, total, good_high=False)
    _breadth_card(b6, "Return+ (3M)", pos_return, total)
    _breadth_card(b7, "Return- (3M)", neg_return, total, good_high=False)
else:
    st.info("Market breadth data unavailable.")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — MARKET HEALTH SCORE
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("📊 Market Health Score")
health_score = 50.0  # default

if not indicators_df.empty:
    n = len(indicators_df)
    pct_ema20   = (indicators_df["close"] > indicators_df["ema20"]).sum() / n * 100
    pct_ema50   = (indicators_df["close"] > indicators_df["ema50"]).sum() / n * 100
    pct_pos_ret = (indicators_df["return_3m"] > 0).sum() / n * 100
    avg_rsi     = float(indicators_df["rsi"].mean())
    rsi_score   = min(max((avg_rsi - 30) / 40 * 100, 0), 100)
    health_score = round(0.25*pct_ema20 + 0.25*pct_ema50 + 0.25*pct_pos_ret + 0.25*rsi_score, 1)

    if health_score >= 70:
        status, bar_color, status_color = "Bullish 🟢", "#00d4aa", "#00d4aa"
    elif health_score >= 30:
        status, bar_color, status_color = "Neutral 🟡", "#ffd700", "#ffd700"
    else:
        status, bar_color, status_color = "Bearish 🔴", "#ff4b4b", "#ff4b4b"

    col_gauge, col_detail = st.columns([1, 1])

    with col_gauge:
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=health_score,
            number={"font": {"size": 48, "color": bar_color}},
            title={"text": f"<b>{status}</b>", "font": {"size": 18, "color": status_color}},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": "#555"},
                "bar": {"color": bar_color, "thickness": 0.25},
                "bgcolor": "#1e2130",
                "borderwidth": 0,
                "steps": [
                    {"range": [0,  30], "color": "#3d1515"},
                    {"range": [30, 70], "color": "#3d3a15"},
                    {"range": [70,100], "color": "#153d2e"},
                ],
                "threshold": {"line": {"color": bar_color, "width": 4}, "value": health_score},
            },
        ))
        fig_gauge.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            height=260, margin=dict(l=20, r=20, t=40, b=20),
        )
        st.plotly_chart(fig_gauge, use_container_width=True)

    with col_detail:
        st.markdown("<br>", unsafe_allow_html=True)
        comps = [
            ("Above EMA20",     pct_ema20,   25),
            ("Above EMA50",     pct_ema50,   25),
            ("Positive 3M Ret", pct_pos_ret, 25),
            ("RSI Strength",    rsi_score,   25),
        ]
        for cname, val, weight in comps:
            contribution = val * weight / 100
            st.markdown(
                f"**{cname}** &nbsp; `{val:.0f}%` &nbsp; → &nbsp; "
                f"<span style='color:{bar_color}'>{contribution:.1f} pts</span>",
                unsafe_allow_html=True,
            )
        st.markdown("---")
        above50_count = int((indicators_df["close"] > indicators_df["ema50"]).sum())
        breadth_str = "strong" if pct_ema20 >= 60 else ("mixed" if pct_ema20 >= 40 else "weak")
        st.info(
            f"{above50_count} of {n} stocks are above EMA50. "
            f"Average RSI is {avg_rsi:.1f}. "
            f"Market breadth is {breadth_str}."
        )
else:
    st.info("Health score unavailable.")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — RELATIVE STRENGTH LEADERBOARD
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("🏆 Relative Strength Ranking")
rs_df = pd.DataFrame()

if not indicators_df.empty:
    rs_df = indicators_df[["ticker", "return_3m", "return_6m", "return_12m"]].copy()
    rs_df = rs_df.dropna(subset=["return_3m"])
    rs_df["return_6m"]  = rs_df["return_6m"].fillna(0)
    rs_df["return_12m"] = rs_df["return_12m"].fillna(0)
    rs_df["RS Score"] = (
        0.40 * rs_df["return_3m"] +
        0.30 * rs_df["return_6m"] +
        0.30 * rs_df["return_12m"]
    ).round(2)
    rs_df = rs_df.sort_values("RS Score", ascending=False).reset_index(drop=True)
    rs_df.index = rs_df.index + 1
    rs_df.index.name = "Rank"

    col_rs_table, col_rs_chart = st.columns([1, 1])

    with col_rs_table:
        styled_rs = rs_df[["ticker", "RS Score", "return_3m", "return_6m", "return_12m"]].style.format({
            "RS Score":    "{:+.2f}",
            "return_3m":  "{:+.2f}%",
            "return_6m":  "{:+.2f}%",
            "return_12m": "{:+.2f}%",
        }).background_gradient(subset=["RS Score"], cmap="RdYlGn")
        st.dataframe(styled_rs, use_container_width=True)

    with col_rs_chart:
        rs_plot = rs_df.sort_values("RS Score")
        bar_colors = ["#00d4aa" if v >= 0 else "#ff4b4b" for v in rs_plot["RS Score"]]
        fig_rs = go.Figure(go.Bar(
            x=rs_plot["RS Score"],
            y=rs_plot["ticker"],
            orientation="h",
            marker_color=bar_colors,
            text=[f"{v:+.1f}" for v in rs_plot["RS Score"]],
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>RS Score: %{x:.2f}<extra></extra>",
        ))
        fig_rs.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=20, r=60, t=20, b=20), height=320,
            xaxis=dict(title="RS Score", zeroline=True, zerolinecolor="#444"),
            yaxis=dict(title=""), showlegend=False,
        )
        st.plotly_chart(fig_rs, use_container_width=True)

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# EXISTING — KPI Metric Cards
# ══════════════════════════════════════════════════════════════════════════════

total_tickers = len(NASDAQ_100_TICKERS)
avg_change = 0.0
top_gainer_label = "N/A"
if not snapshot_df.empty:
    avg_change = round(float(snapshot_df["change_pct"].mean()), 2)
if not gainers_df.empty:
    top_row = gainers_df.iloc[0]
    sign = "+" if top_row["change_pct"] >= 0 else ""
    top_gainer_label = f"{top_row['ticker']}  {sign}{top_row['change_pct']:.2f}%"

col_m1, col_m2, col_m3 = st.columns(3)
with col_m1:
    st.metric(label="📌 Total Tickers Tracked", value=total_tickers)
with col_m2:
    direction = "Bullish 🟢" if avg_change >= 0 else "Bearish 🔴"
    st.metric(label="📈 Market Avg Change",
              value=f"{avg_change:+.2f}%",
              delta=direction)
with col_m3:
    st.metric(label="🏆 Top Gainer", value=top_gainer_label)

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# EXISTING — Top Movers + Sector Treemap
# ══════════════════════════════════════════════════════════════════════════════

col_left, col_right = st.columns([6, 4])

with col_left:
    st.subheader("📊 Top 5 Gainers & Losers")
    if not gainers_df.empty and not losers_df.empty:
        movers_df = pd.concat([gainers_df, losers_df], ignore_index=True)
        movers_df = movers_df.drop_duplicates(subset="ticker").sort_values("change_pct")
        colors = ["#00d4aa" if v >= 0 else "#ff4b4b" for v in movers_df["change_pct"]]
        fig_bar = go.Figure(go.Bar(
            x=movers_df["change_pct"], y=movers_df["ticker"], orientation="h",
            marker_color=colors,
            text=[f"{v:+.2f}%" for v in movers_df["change_pct"]],
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>Change: %{x:.2f}%<extra></extra>",
        ))
        fig_bar.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=20, r=60, t=20, b=20),
            xaxis=dict(title="Daily Change (%)", zeroline=True, zerolinecolor="#444"),
            yaxis=dict(title=""), height=320, showlegend=False,
        )
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("Mover data unavailable.")

with col_right:
    st.subheader("🗺️ Sector Heatmap")
    if not sector_df.empty:
        fig_tree = px.treemap(
            sector_df, path=["sector"], values="ticker_count", color="avg_change",
            color_continuous_scale=[[0.0,"#ff4b4b"],[0.5,"#1e2130"],[1.0,"#00d4aa"]],
            color_continuous_midpoint=0,
            custom_data=["avg_change", "ticker_count"],
        )
        fig_tree.update_traces(
            texttemplate="<b>%{label}</b><br>%{customdata[0]:+.2f}%",
            hovertemplate="<b>%{label}</b><br>Avg Change: %{customdata[0]:+.2f}%<br>Tickers: %{customdata[1]}<extra></extra>",
        )
        fig_tree.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=20, b=0), height=320, coloraxis_showscale=False,
        )
        st.plotly_chart(fig_tree, use_container_width=True)
    else:
        st.info("Sector data unavailable.")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# EXISTING — Full Ticker Table
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("📋 Nasdaq 100 Watchlist")

if not display_df.empty:
    def _colour_change(val: float) -> str:
        colour = "#00d4aa" if val >= 0 else "#ff4b4b"
        return f"color: {colour}; font-weight: 600"

    styled = (
        display_df.style
        .map(_colour_change, subset=["Change %"])
        .format({"Price": "${:.2f}", "Change %": "{:+.2f}%", "Volume": "{:,.0f}"})
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.caption("Click a ticker below to open its detail page:")
    btn_cols = st.columns(len(display_df))
    for i, row in display_df.reset_index(drop=True).iterrows():
        ticker_btn = row["Ticker"]
        with btn_cols[i]:
            if st.button(f"🔍 {ticker_btn}", key=f"analyze_{ticker_btn}"):
                st.session_state["selected_ticker"] = ticker_btn
                st.switch_page("pages/2_Stock_Detail.py")
else:
    st.warning("No ticker data available.")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 3 — CORRELATION MATRIX
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("🔗 Correlation Matrix")

if not corr_df.empty:
    tickers_c = corr_df.columns.tolist()
    z = corr_df.values.round(2)

    fig_corr = go.Figure(go.Heatmap(
        z=z, x=tickers_c, y=tickers_c,
        colorscale=[
            [0.0, "#ff4b4b"], [0.5, "#1e2130"], [1.0, "#00d4aa"],
        ],
        zmin=-1, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in z],
        texttemplate="%{text}",
        textfont={"size": 11},
        hovertemplate="<b>%{x} vs %{y}</b><br>Correlation: %{z:.2f}<extra></extra>",
    ))
    fig_corr.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=20, b=20), height=420,
    )
    st.plotly_chart(fig_corr, use_container_width=True)

    # Auto insight
    n_t = len(tickers_c)
    mask = [[i != j for j in range(n_t)] for i in range(n_t)]
    mask_df = pd.DataFrame(mask, index=tickers_c, columns=tickers_c)
    corr_masked = corr_df.where(mask_df)
    max_pair = corr_masked.stack().idxmax()
    min_pair = corr_masked.stack().idxmin()
    max_val  = round(float(corr_masked.loc[max_pair]), 2)
    min_val  = round(float(corr_masked.loc[min_pair]), 2)
    st.info(
        f"🔗 **Strongest correlation:** {max_pair[0]} & {max_pair[1]} ({max_val:+.2f})  |  "
        f"**Weakest:** {min_pair[0]} & {min_pair[1]} ({min_val:+.2f})"
    )
else:
    st.info("Correlation data unavailable.")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 4 — RISK VS RETURN MAP
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("⚖️ Risk vs Return Analysis")

if not risk_df.empty:
    avg_vol_norm = risk_df["avg_volume"].max()
    bubble_size_list = (risk_df["avg_volume"] / avg_vol_norm * 50 + 10).clip(10, 60).tolist()
    mid_ret = float(risk_df["ann_return"].median())
    mid_vol = float(risk_df["ann_vol"].median())
    xmax = float(risk_df["ann_vol"].max() * 1.3)
    ymin = float(risk_df["ann_return"].min() * 1.3)
    ymax = float(risk_df["ann_return"].max() * 1.3)

    fig_rr = go.Figure()

    quadrants = [
        (0, mid_vol, mid_ret, ymax,  "High Return / Low Risk",  "rgba(0,212,170,0.06)"),
        (mid_vol, xmax, mid_ret, ymax, "High Return / High Risk", "rgba(255,215,0,0.06)"),
        (0, mid_vol, ymin, mid_ret,  "Low Return / Low Risk",   "rgba(100,100,100,0.06)"),
        (mid_vol, xmax, ymin, mid_ret, "Low Return / High Risk", "rgba(255,75,75,0.06)"),
    ]
    for (x0, x1, y0, y1, label, fill) in quadrants:
        fig_rr.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                         fillcolor=fill, line_width=0)
        fig_rr.add_annotation(
            x=(x0+x1)/2, y=(y0+y1)/2, text=label,
            showarrow=False, font=dict(size=9, color="#555"), opacity=0.8,
        )

    fig_rr.add_vline(x=mid_vol, line_dash="dash", line_color="#444", opacity=0.6)
    fig_rr.add_hline(y=mid_ret, line_dash="dash", line_color="#444", opacity=0.6)

    for idx, (_, row) in enumerate(risk_df.iterrows()):
        dot_color = (
            "#00d4aa" if row["ann_return"] >= mid_ret and row["ann_vol"] <= mid_vol else
            "#ffd700"  if row["ann_return"] >= mid_ret else
            "#ff4b4b"  if row["ann_return"] <  mid_ret and row["ann_vol"] > mid_vol else
            "#8b8b8b"
        )
        fig_rr.add_trace(go.Scatter(
            x=[row["ann_vol"]], y=[row["ann_return"]],
            mode="markers+text",
            marker=dict(
                size=bubble_size_list[idx],
                color=dot_color, opacity=0.85,
                line=dict(width=1, color="#0e1117"),
            ),
            text=[row["ticker"]], textposition="top center",
            textfont=dict(size=11, color="#fafafa"),
            name=row["ticker"],
            hovertemplate=(
                f"<b>{row['ticker']}</b><br>"
                f"Ann Return: {row['ann_return']:+.1f}%<br>"
                f"Ann Vol: {row['ann_vol']:.1f}%<br>"
                f"Sharpe: {row['sharpe']:.2f}<extra></extra>"
            ),
        ))

    fig_rr.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="Annualised Volatility (%)", gridcolor="#2a2a2a"),
        yaxis=dict(title="Annualised Return (%)", gridcolor="#2a2a2a"),
        height=460, showlegend=False,
        margin=dict(l=20, r=20, t=20, b=40),
    )
    st.plotly_chart(fig_rr, use_container_width=True)

    best_sharpe = risk_df.loc[risk_df["sharpe"].idxmax()]
    best_return = risk_df.loc[risk_df["ann_return"].idxmax()]
    highest_vol = risk_df.loc[risk_df["ann_vol"].idxmax()]
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("🏅 Best Risk-Adjusted (Sharpe)", best_sharpe["ticker"],
                  f"{best_sharpe['sharpe']:.2f}")
    with c2:
        st.metric("📈 Highest Return", best_return["ticker"],
                  f"{best_return['ann_return']:+.1f}%")
    with c3:
        st.metric("⚡ Highest Volatility", highest_vol["ticker"],
                  f"{highest_vol['ann_vol']:.1f}%")
else:
    st.info("Risk/return data unavailable.")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# EXISTING — Data Quality Report
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("📋 Data Quality Report", expanded=False):
    if quality_reports:
        avg_score = sum(r["quality_score"] for r in quality_reports) / len(quality_reports)
        score_delta = "Excellent 🟢" if avg_score >= 90 else ("Acceptable 🟡" if avg_score >= 70 else "Needs Attention 🔴")
        q_col1, q_col2 = st.columns([1, 3])
        with q_col1:
            st.metric(label="Avg Quality Score", value=f"{avg_score:.1f} / 100", delta=score_delta)
        with q_col2:
            st.caption(
                f"Reports generated for {len(quality_reports)} tickers. "
                "Each score starts at 100 and is penalised for schema issues, "
                "missing values, duplicates, and volume outliers."
            )
        st.divider()
        tab_labels = [r["ticker"] for r in quality_reports]
        tabs = st.tabs(tab_labels)
        for tab, report in zip(tabs, quality_reports):
            with tab:
                score = report["quality_score"]
                colour = "🟢" if score >= 90 else ("🟡" if score >= 70 else "🔴")
                st.metric(label="Quality Score", value=f"{colour} {score:.1f} / 100")
                st.json(report)
    else:
        st.info("No quality reports available.")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 6 — MARKET INTELLIGENCE SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("🧠 Market Intelligence Summary")

if not indicators_df.empty and not risk_df.empty and not rs_df.empty:
    n_sum = len(indicators_df)
    above50_sum = int((indicators_df["close"] > indicators_df["ema50"]).sum())
    avg_rsi_sum = float(indicators_df["rsi"].mean())
    above_ema20_sum = int((indicators_df["close"] > indicators_df["ema20"]).sum())
    above_ema50_sum = int((indicators_df["close"] > indicators_df["ema50"]).sum())
    above_ema200_sum = int((indicators_df["close"] > indicators_df["ema200"]).sum())
    top_rs_ticker = rs_df.iloc[0]["ticker"] if not rs_df.empty else "N/A"
    bottom_rs_ticker = rs_df.iloc[-1]["ticker"] if not rs_df.empty else "N/A"
    best_sh_ticker = risk_df.loc[risk_df["sharpe"].idxmax()]["ticker"] if not risk_df.empty else "N/A"
    best_sh_val = float(risk_df["sharpe"].max()) if not risk_df.empty else 0.0

    corr_note = ""
    if not corr_df.empty:
        n_t2 = len(corr_df.columns)
        tc2 = corr_df.columns.tolist()
        mask2 = pd.DataFrame([[i != j for j in range(n_t2)] for i in range(n_t2)],
                              index=tc2, columns=tc2)
        cv2 = corr_df.where(mask2)
        mp2 = cv2.stack().idxmax()
        mv2 = round(float(cv2.loc[mp2]), 2)
        corr_note = f"{mp2[0]} and {mp2[1]} show the highest co-movement ({mv2:+.2f})."

    condition = "Bullish" if health_score >= 70 else ("Neutral" if health_score >= 30 else "Bearish")
    bullets = [
        f"**Market condition:** Health Score {health_score:.0f}/100 → {condition}. "
        f"{above50_sum}/{n_sum} stocks are trading above EMA50.",

        f"**Leadership:** {top_rs_ticker} leads the Relative Strength ranking. "
        f"{bottom_rs_ticker} shows the weakest momentum over the past 12 months.",

        f"**Risk environment:** {best_sh_ticker} offers the best risk-adjusted return "
        f"(Sharpe: {best_sh_val:.2f}). "
        f"Average RSI across the portfolio is {avg_rsi_sum:.1f}.",

        (f"**Correlation:** {corr_note}" if corr_note
         else "**Correlation:** Correlation computed from daily returns."),

        f"**Market breadth:** {above_ema20_sum}/{n_sum} above EMA20, "
        f"{above_ema50_sum}/{n_sum} above EMA50, "
        f"{above_ema200_sum}/{n_sum} above EMA200. "
        "⚠️ For educational purposes only — not investment advice.",
    ]
    for b in bullets:
        st.markdown(f"• {b}")
else:
    st.info("Insufficient data for market summary.")

st.caption("⚠️ NasdaqPulse is for educational purposes only. Not investment advice.")
