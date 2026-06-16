"""
pages/1_Overview.py
===================
NasdaqPulse — Page 1: Nasdaq 100 Real-Time Overview

Layout:
  Row 1 — 3 KPI metric cards
  Row 2 — Top movers bar chart (left) + Sector treemap (right)
  Row 3 — Full ticker table with Styler colour-coding
  Row 4 — Data quality report expander
"""

from __future__ import annotations

from typing import List

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from config.settings import NASDAQ_100_TICKERS, TICKER_INFO
from utils.mongodb_client import load_ticker_metadata, save_ticker_metadata, create_text_index
from utils.yfinance_loader import get_latest_snapshot, fetch_multiple_tickers
from utils.duckdb_engine import get_top_movers, get_sector_summary
from utils.data_quality import generate_quality_report
from utils.logger import log_info, log_error

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Overview · NasdaqPulse",
    page_icon="📊",
    layout="wide",
)

# ── Header ────────────────────────────────────────────────────────────────────
st.title("NasdaqPulse 📈")
st.caption("Nasdaq 100 Real-Time Overview")
st.divider()

# ── Session state defaults ────────────────────────────────────────────────────
if "selected_ticker" not in st.session_state:
    st.session_state["selected_ticker"] = "NVDA"
if "period" not in st.session_state:
    st.session_state["period"] = "1y"


# ── Helper: build metadata from TICKER_INFO if MongoDB is empty ───────────────

def _build_local_metadata() -> pd.DataFrame:
    """Build a minimal metadata DataFrame from the local TICKER_INFO constant.

    Returns:
        DataFrame with columns: ticker, name, sector.
    """
    rows = [
        {"ticker": t, "name": info["name"], "sector": info["sector"]}
        for t, info in TICKER_INFO.items()
    ]
    return pd.DataFrame(rows)


# ── Data loading ──────────────────────────────────────────────────────────────

with st.spinner("กำลังโหลดข้อมูลตลาด..."):
    try:
        # 1. Metadata from MongoDB (fallback to local dict)
        metadata_df: pd.DataFrame = load_ticker_metadata()
        if metadata_df.empty:
            log_error("MongoDB metadata empty — using local TICKER_INFO fallback.")
            metadata_df = _build_local_metadata()
            # Try to seed MongoDB for next time
            try:
                create_text_index()
                save_ticker_metadata(NASDAQ_100_TICKERS)
            except Exception:
                pass

        # Ensure required columns exist
        for col in ["ticker", "name", "sector"]:
            if col not in metadata_df.columns:
                metadata_df = _build_local_metadata()
                break

        # 2. Latest price snapshot (cached 15 min)
        snapshot_df: pd.DataFrame = get_latest_snapshot(NASDAQ_100_TICKERS)

        # 3. Top movers via DuckDB
        gainers_df: pd.DataFrame
        losers_df: pd.DataFrame
        gainers_df, losers_df = get_top_movers(snapshot_df, n=5)

        # 4. Sector summary via DuckDB JOIN
        sector_df: pd.DataFrame = get_sector_summary(metadata_df, snapshot_df)

        # 5. Merged display table
        display_df: pd.DataFrame = pd.DataFrame()
        if not snapshot_df.empty and not metadata_df.empty:
            display_df = snapshot_df.merge(
                metadata_df[["ticker", "name", "sector"]],
                on="ticker",
                how="left",
            )
            display_df = display_df.rename(columns={
                "ticker": "Ticker",
                "name": "Company",
                "sector": "Sector",
                "close": "Price",
                "change_pct": "Change %",
                "volume": "Volume",
            })
            display_df = display_df[["Ticker", "Company", "Sector", "Price", "Change %", "Volume"]]

        # 6. Data quality batch report
        quality_reports: List[dict] = []
        ohlcv_batch = fetch_multiple_tickers(NASDAQ_100_TICKERS, period="5d")
        for ticker, df in ohlcv_batch.items():
            if not df.empty:
                quality_reports.append(generate_quality_report(df, ticker))

        log_info("Overview page data loaded successfully.")

    except Exception as exc:
        st.error(f"⚠️ Data loading error: {exc}")
        log_error(f"Overview page load failed: {exc}")
        st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# ROW 1 — KPI Metric Cards
# ══════════════════════════════════════════════════════════════════════════════

total_tickers: int = len(NASDAQ_100_TICKERS)

avg_change: float = 0.0
top_gainer_label: str = "N/A"

if not snapshot_df.empty:
    avg_change = round(float(snapshot_df["change_pct"].mean()), 2)

if not gainers_df.empty:
    top_row = gainers_df.iloc[0]
    top_gainer_label = f"{top_row['ticker']}  {top_row['change_pct']:+.2f}%"

col_m1, col_m2, col_m3 = st.columns(3)

with col_m1:
    st.metric(
        label="📌 Total Tickers Tracked",
        value=total_tickers,
    )

with col_m2:
    st.metric(
        label="📈 Market Avg Change",
        value=f"{avg_change:+.2f}%",
        delta=f"{'Bullish 🟢' if avg_change >= 0 else 'Bearish 🔴'}",
    )

with col_m3:
    st.metric(
        label="🏆 Top Gainer",
        value=top_gainer_label,
    )

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# ROW 2 — Charts
# ══════════════════════════════════════════════════════════════════════════════

col_left, col_right = st.columns([6, 4])

# ── Left: Top 5 Gainers + Top 5 Losers horizontal bar ────────────────────────
with col_left:
    st.subheader("📊 Top 5 Gainers & Losers")

    if not gainers_df.empty and not losers_df.empty:
        movers_df: pd.DataFrame = pd.concat([gainers_df, losers_df], ignore_index=True)
        # Deduplicate in case a ticker appears in both (edge case with <10 tickers)
        movers_df = movers_df.drop_duplicates(subset="ticker")
        movers_df = movers_df.sort_values("change_pct")

        colors: List[str] = [
            "#00d4aa" if v >= 0 else "#ff4b4b"
            for v in movers_df["change_pct"]
        ]

        fig_bar = go.Figure(
            go.Bar(
                x=movers_df["change_pct"],
                y=movers_df["ticker"],
                orientation="h",
                marker_color=colors,
                text=[f"{v:+.2f}%" for v in movers_df["change_pct"]],
                textposition="outside",
                hovertemplate="<b>%{y}</b><br>Change: %{x:.2f}%<extra></extra>",
            )
        )
        fig_bar.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=20, r=60, t=20, b=20),
            xaxis=dict(title="Daily Change (%)", zeroline=True, zerolinecolor="#444"),
            yaxis=dict(title=""),
            height=320,
            showlegend=False,
        )
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("Mover data unavailable.")

# ── Right: Sector Treemap ─────────────────────────────────────────────────────
with col_right:
    st.subheader("🗺️ Sector Heatmap")

    if not sector_df.empty:
        fig_tree = px.treemap(
            sector_df,
            path=["sector"],
            values="ticker_count",
            color="avg_change",
            color_continuous_scale=[
                [0.0, "#ff4b4b"],
                [0.5, "#1e2130"],
                [1.0, "#00d4aa"],
            ],
            color_continuous_midpoint=0,
            custom_data=["avg_change", "ticker_count"],
        )
        fig_tree.update_traces(
            texttemplate="<b>%{label}</b><br>%{customdata[0]:+.2f}%",
            hovertemplate=(
                "<b>%{label}</b><br>"
                "Avg Change: %{customdata[0]:+.2f}%<br>"
                "Tickers: %{customdata[1]}<extra></extra>"
            ),
        )
        fig_tree.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=20, b=0),
            height=320,
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig_tree, use_container_width=True)
    else:
        st.info("Sector data unavailable.")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# ROW 3 — Full Ticker Table
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("📋 Nasdaq 100 Watchlist")

if not display_df.empty:

    # ── Pandas Styler: colour Change % column ─────────────────────────────────
    def _colour_change(val: float) -> str:
        """Return green CSS for positive values, red for negative."""
        colour = "#00d4aa" if val >= 0 else "#ff4b4b"
        return f"color: {colour}; font-weight: 600"

    styled = (
        display_df.style
        .map(_colour_change, subset=["Change %"])
        .format({
            "Price": "${:.2f}",
            "Change %": "{:+.2f}%",
            "Volume": "{:,.0f}",
        })
    )

    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price": st.column_config.NumberColumn(
                "Price",
                format="$%.2f",
            ),
            "Change %": st.column_config.NumberColumn(
                "Change %",
                format="%.2f%%",
            ),
            "Volume": st.column_config.NumberColumn(
                "Volume",
                format="%d",
            ),
        },
    )

    # ── Per-ticker Analyze button ─────────────────────────────────────────────
    st.caption("Click a ticker below to open its detail page:")
    btn_cols = st.columns(len(display_df))
    for i, row in display_df.reset_index(drop=True).iterrows():
        ticker = row["Ticker"]
        with btn_cols[i]:
            if st.button(f"🔍 {ticker}", key=f"analyze_{ticker}"):
                st.session_state["selected_ticker"] = ticker
                st.switch_page("pages/2_Stock_Detail.py")

else:
    st.warning("No ticker data available. Check your network connection and try refreshing.")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# ROW 4 — Data Quality Report
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("📋 Data Quality Report", expanded=False):
    if quality_reports:
        avg_score: float = sum(r["quality_score"] for r in quality_reports) / len(quality_reports)

        # Score colour
        if avg_score >= 90:
            score_delta = "Excellent 🟢"
        elif avg_score >= 70:
            score_delta = "Acceptable 🟡"
        else:
            score_delta = "Needs Attention 🔴"

        q_col1, q_col2 = st.columns([1, 3])
        with q_col1:
            st.metric(
                label="Avg Quality Score",
                value=f"{avg_score:.1f} / 100",
                delta=score_delta,
            )
        with q_col2:
            st.caption(
                f"Reports generated for {len(quality_reports)} tickers. "
                "Each score starts at 100 and is penalised for schema issues, "
                "missing values, duplicates, and volume outliers."
            )

        st.divider()

        # Show individual report per ticker in tabs
        tab_labels: List[str] = [r["ticker"] for r in quality_reports]
        tabs = st.tabs(tab_labels)
        for tab, report in zip(tabs, quality_reports):
            with tab:
                score = report["quality_score"]
                colour = "🟢" if score >= 90 else ("🟡" if score >= 70 else "🔴")
                st.metric(
                    label="Quality Score",
                    value=f"{colour} {score:.1f} / 100",
                )
                st.json(report)
    else:
        st.info("No quality reports available — data fetch may have failed.")
