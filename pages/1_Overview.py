"""pages/1_Overview.py — NasdaqPulse Market Intelligence Overview"""
from __future__ import annotations
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

st.set_page_config(page_title="Overview · NasdaqPulse", page_icon="\U0001f4ca", layout="wide")

if "selected_ticker" not in st.session_state:
    st.session_state["selected_ticker"] = "NVDA"
if "period" not in st.session_state:
    st.session_state["period"] = "1y"

st.title("NasdaqPulse \U0001f4c8")
st.caption("Nasdaq 100 Market Intelligence Overview")
st.divider()


def _build_local_metadata() -> pd.DataFrame:
    rows = [{"ticker": t, "name": info["name"], "sector": info["sector"]}
            for t, info in TICKER_INFO.items()]
    return pd.DataFrame(rows)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_all_ohlcv() -> Dict[str, pd.DataFrame]:
    return fetch_multiple_tickers(NASDAQ_100_TICKERS, period="1y")


# DATA LOADING
with st.spinner("กำลังโหลดข้อมูลตลาด..."):
    try:
        metadata_df: pd.DataFrame = load_ticker_metadata()
        if metadata_df.empty:
            log_error("MongoDB metadata empty -- using local fallback.")
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
        st.error(f"Data loading error: {exc}")
        log_error(f"Overview page load failed: {exc}")
        st.stop()


# ─────────────────────────────────────────────────────────
# 1. NasdaqPulse Watchlist
# ─────────────────────────────────────────────────────────
st.subheader("\U0001f4cb NasdaqPulse Watchlist")

if not display_df.empty:
    def _colour_pct(val: float) -> str:
        colour = "#00d4aa" if val >= 0 else "#ff4b4b"
        return "color:" + colour + ";font-weight:600"

    styled_tbl = (
        display_df.style
        .map(_colour_pct, subset=["Change %"])
        .format({"Price": "${:.2f}", "Change %": "{:+.2f}%", "Volume": "{:,.0f}"})
    )
    st.dataframe(styled_tbl, use_container_width=True, hide_index=True)
    st.caption("Click a ticker below to open its detail page:")
    btn_cols = st.columns(len(display_df))
    for i_row, row in display_df.reset_index(drop=True).iterrows():
        ticker_btn = row["Ticker"]
        with btn_cols[i_row]:
            if st.button("\U0001f50d " + ticker_btn, key="analyze_" + ticker_btn):
                st.session_state["selected_ticker"] = ticker_btn
                st.switch_page("pages/2_Stock_Detail.py")
else:
    st.warning("No ticker data available.")

st.divider()


# ─────────────────────────────────────────────────────────
# 2. Top 5 Gainers & Losers
# ─────────────────────────────────────────────────────────
st.subheader("\U0001f4ca Top 5 Gainers & Losers")

if not gainers_df.empty and not losers_df.empty:
    movers_df = pd.concat([gainers_df, losers_df], ignore_index=True)
    movers_df = movers_df.drop_duplicates(subset="ticker").sort_values("change_pct")
    colors_mv = ["#00d4aa" if v >= 0 else "#ff4b4b" for v in movers_df["change_pct"]]
    fig_bar = go.Figure(go.Bar(
        x=movers_df["change_pct"], y=movers_df["ticker"], orientation="h",
        marker_color=colors_mv,
        text=[f"{v:+.2f}%" for v in movers_df["change_pct"]],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>Change: %{x:.2f}%<extra></extra>",
    ))
    fig_bar.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=80, t=20, b=20),
        xaxis=dict(title="Daily Change (%)", zeroline=True, zerolinecolor="#444"),
        yaxis=dict(title=""), height=340, showlegend=False,
    )
    st.plotly_chart(fig_bar, use_container_width=True)
else:
    st.info("Mover data unavailable.")

st.divider()


# ─────────────────────────────────────────────────────────
# 3. Risk vs Return Analysis
# ─────────────────────────────────────────────────────────
st.subheader("⚖️ Risk vs Return Analysis")

if not risk_df.empty:
    avg_vol_norm = float(risk_df["avg_volume"].max())
    bubble_sizes = (risk_df["avg_volume"] / avg_vol_norm * 50 + 10).clip(10, 60).tolist()
    mid_ret = float(risk_df["ann_return"].median())
    mid_vol = float(risk_df["ann_vol"].median())
    x_max   = float(risk_df["ann_vol"].max() * 1.3)
    y_min   = float(risk_df["ann_return"].min() * 1.3)
    y_max   = float(risk_df["ann_return"].max() * 1.3)

    fig_rr = go.Figure()
    for x0, x1, y0, y1, label, fill in [
        (0,       mid_vol, mid_ret, y_max,  "High Return / Low Risk",  "rgba(0,212,170,0.06)"),
        (mid_vol, x_max,  mid_ret, y_max,  "High Return / High Risk", "rgba(255,215,0,0.06)"),
        (0,       mid_vol, y_min,  mid_ret, "Low Return / Low Risk",   "rgba(100,100,100,0.06)"),
        (mid_vol, x_max,  y_min,  mid_ret, "Low Return / High Risk",  "rgba(255,75,75,0.06)"),
    ]:
        fig_rr.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=fill, line_width=0)
        fig_rr.add_annotation(x=(x0+x1)/2, y=(y0+y1)/2, text=label,
                               showarrow=False, font=dict(size=9, color="#555"), opacity=0.8)

    fig_rr.add_vline(x=mid_vol, line_dash="dash", line_color="#444", opacity=0.6)
    fig_rr.add_hline(y=mid_ret, line_dash="dash", line_color="#444", opacity=0.6)

    for idx, (_, row) in enumerate(risk_df.iterrows()):
        if row["ann_return"] >= mid_ret and row["ann_vol"] <= mid_vol:
            dc = "#00d4aa"
        elif row["ann_return"] >= mid_ret:
            dc = "#ffd700"
        elif row["ann_return"] < mid_ret and row["ann_vol"] > mid_vol:
            dc = "#ff4b4b"
        else:
            dc = "#8b8b8b"
        fig_rr.add_trace(go.Scatter(
            x=[row["ann_vol"]], y=[row["ann_return"]],
            mode="markers+text",
            marker=dict(size=bubble_sizes[idx], color=dc, opacity=0.85,
                        line=dict(width=1, color="#0e1117")),
            text=[row["ticker"]], textposition="top center",
            textfont=dict(size=11, color="#fafafa"),
            name=row["ticker"],
            hovertemplate=(
                "<b>" + str(row["ticker"]) + "</b><br>"
                "Ann Return: " + f"{row['ann_return']:+.1f}" + "%<br>"
                "Ann Vol: " + f"{row['ann_vol']:.1f}" + "%<br>"
                "Sharpe: " + f"{row['sharpe']:.2f}" + "<extra></extra>"
            ),
        ))

    fig_rr.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="Annualised Volatility (%)", gridcolor="#2a2a2a"),
        yaxis=dict(title="Annualised Return (%)",     gridcolor="#2a2a2a"),
        height=460, showlegend=False,
        margin=dict(l=20, r=20, t=20, b=40),
    )
    st.plotly_chart(fig_rr, use_container_width=True)

    best_sharpe = risk_df.loc[risk_df["sharpe"].idxmax()]
    best_return = risk_df.loc[risk_df["ann_return"].idxmax()]
    highest_vol = risk_df.loc[risk_df["ann_vol"].idxmax()]
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("\U0001f3c5 Best Risk-Adjusted (Sharpe)",
                  best_sharpe["ticker"], f"{best_sharpe['sharpe']:.2f}")
    with c2:
        st.metric("\U0001f4c8 Highest Return",
                  best_return["ticker"], f"{best_return['ann_return']:+.1f}%")
    with c3:
        st.metric("⚡ Highest Volatility",
                  highest_vol["ticker"], f"{highest_vol['ann_vol']:.1f}%")
else:
    st.info("Risk/return data unavailable.")

st.divider()


# ─────────────────────────────────────────────────────────
# 4. Relative Strength Ranking
# ─────────────────────────────────────────────────────────
st.subheader("\U0001f3c6 Relative Strength Ranking")
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
        def _color_rs(val: float) -> str:
            if val > 10:
                return "background-color:#1a4a38;color:#00d4aa;font-weight:600"
            if val > 0:
                return "background-color:#1a3a2a;color:#6fcfa0"
            if val > -10:
                return "background-color:#3a1a1a;color:#ff8080"
            return "background-color:#4a1a1a;color:#ff4b4b;font-weight:600"

        rs_display = rs_df[["ticker", "RS Score", "return_3m", "return_6m", "return_12m"]].copy()
        styled_rs = (
            rs_display.style
            .format({"RS Score": "{:+.2f}", "return_3m": "{:+.2f}%",
                     "return_6m": "{:+.2f}%", "return_12m": "{:+.2f}%"})
            .map(_color_rs, subset=["RS Score"])
        )
        st.dataframe(styled_rs, use_container_width=True)

    with col_rs_chart:
        rs_plot = rs_df.sort_values("RS Score")
        bar_colors_rs = ["#00d4aa" if v >= 0 else "#ff4b4b" for v in rs_plot["RS Score"]]
        fig_rs = go.Figure(go.Bar(
            x=rs_plot["RS Score"], y=rs_plot["ticker"], orientation="h",
            marker_color=bar_colors_rs,
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


# ─────────────────────────────────────────────────────────
# 5. Market Breadth Panel
# ─────────────────────────────────────────────────────────
st.subheader("\U0001f4e1 Market Breadth Panel")

above_ema20 = above_ema50 = above_ema200 = 0
rsi_ob = rsi_os = pos_return = neg_return = total = 0

if not indicators_df.empty:
    total        = len(indicators_df)
    above_ema20  = int((indicators_df["close"] > indicators_df["ema20"]).sum())
    above_ema50  = int((indicators_df["close"] > indicators_df["ema50"]).sum())
    above_ema200 = int((indicators_df["close"] > indicators_df["ema200"]).sum())
    rsi_ob       = int((indicators_df["rsi"] > 70).sum())
    rsi_os       = int((indicators_df["rsi"] < 30).sum())
    pos_return   = int((indicators_df["return_3m"] > 0).sum())
    neg_return   = total - pos_return

    def _card_html(label, val, total_n, good_high=True):
        pct = val / total_n * 100 if total_n else 0
        good = (good_high and val >= total_n / 2) or (not good_high and val <= total_n / 2)
        color = "#00d4aa" if good else "#ff4b4b"
        return (
            '<div style="background:#161b22;border-radius:8px;padding:14px 10px;text-align:center">'
            '<div style="font-size:11px;color:#8b949e;margin-bottom:4px">' + label + '</div>'
            '<div style="font-size:28px;font-weight:700;color:' + color + '">' + str(val) +
            '<span style="font-size:14px;color:#8b949e">/' + str(total_n) + '</span></div>'
            '<div style="font-size:12px;color:#8b949e">' + f"{pct:.0f}%" + '</div></div>'
        )

    b1, b2, b3, b4, b5, b6, b7 = st.columns(7)
    for col_w, lbl, val_b, gh in [
        (b1, "Above EMA20",  above_ema20,  True),
        (b2, "Above EMA50",  above_ema50,  True),
        (b3, "Above EMA200", above_ema200, True),
        (b4, "RSI > 70",     rsi_ob,       False),
        (b5, "RSI < 30",     rsi_os,       False),
        (b6, "Return+ (3M)", pos_return,   True),
        (b7, "Return- (3M)", neg_return,   False),
    ]:
        with col_w:
            st.markdown(_card_html(lbl, val_b, total, gh), unsafe_allow_html=True)
else:
    st.info("Market breadth data unavailable.")

st.divider()


# ─────────────────────────────────────────────────────────
# 6. Correlation Matrix
# ─────────────────────────────────────────────────────────
st.subheader("\U0001f517 Correlation Matrix")

if not corr_df.empty:
    tickers_c = corr_df.columns.tolist()
    z_vals = corr_df.values.round(2)
    fig_corr = go.Figure(go.Heatmap(
        z=z_vals, x=tickers_c, y=tickers_c,
        colorscale=[[0.0, "#ff4b4b"], [0.5, "#1e2130"], [1.0, "#00d4aa"]],
        zmin=-1, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in z_vals],
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

    n_tc = len(tickers_c)
    mask_data = [[p != q for q in range(n_tc)] for p in range(n_tc)]
    mask_df_c = pd.DataFrame(mask_data, index=tickers_c, columns=tickers_c)
    corr_masked = corr_df.where(mask_df_c)
    max_pair = corr_masked.stack().idxmax()
    min_pair = corr_masked.stack().idxmin()
    max_cv = round(float(corr_masked.loc[max_pair]), 2)
    min_cv = round(float(corr_masked.loc[min_pair]), 2)
    st.info(
        f"Strongest correlation: {max_pair[0]} & {max_pair[1]} ({max_cv:+.2f}) | "
        f"Weakest: {min_pair[0]} & {min_pair[1]} ({min_cv:+.2f})"
    )
else:
    st.info("Correlation data unavailable.")

st.caption("⚠️ NasdaqPulse is for educational purposes only. Not investment advice.")
