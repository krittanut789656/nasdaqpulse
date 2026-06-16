"""
pages/2_Stock_Detail.py
=======================
NasdaqPulse — Page 2: Stock Detail Dashboard

Non-AI Mode:
  KPI row (price · return · volume · RSI)
  Plotly make_subplots: Candlestick + EMA lines | Volume bars | RSI line
  OHLCV data table expander

AI Mode:
  Same Plotly chart
  Groq llama-3.1-8b-instant → Thai narrative analysis (5-7 sentences)

Data flow:
  Snowflake (primary) → yfinance fallback → Pandas indicators → DuckDB KPIs
"""

from __future__ import annotations

import json
import time
from typing import Dict

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from groq import Groq

from config.settings import (
    NASDAQ_100_TICKERS,
    TICKER_INFO,
    GROQ_API_KEY,
    GROQ_MODEL,
)
from utils.snowflake_client import load_ohlcv_from_snowflake, save_ohlcv_to_snowflake
from utils.yfinance_loader import fetch_ohlcv
from utils.duckdb_engine import get_kpi_stats
from utils.logger import log_info, log_error, log_warning

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Detail · NasdaqPulse",
    page_icon="🕯️",
    layout="wide",
)

# ── Session state defaults ────────────────────────────────────────────────────
if "selected_ticker" not in st.session_state:
    st.session_state["selected_ticker"] = "NVDA"
if "period" not in st.session_state:
    st.session_state["period"] = "1y"
if "ai_mode" not in st.session_state:
    st.session_state["ai_mode"] = False


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## 🕯️ Stock Detail")
    st.divider()

    # Ticker selector
    ticker_labels = [
        f"{t} — {TICKER_INFO[t]['name']}" for t in NASDAQ_100_TICKERS
    ]
    default_idx = NASDAQ_100_TICKERS.index(st.session_state["selected_ticker"])
    selected_label = st.selectbox(
        "🔍 Select Ticker",
        options=ticker_labels,
        index=default_idx,
        key="detail_ticker_select",
    )
    selected_ticker: str = selected_label.split(" — ")[0]
    st.session_state["selected_ticker"] = selected_ticker

    # Period selector — guard against values set by app.py that aren't in this list
    period_options = ["3mo", "6mo", "1y", "3y"]
    _saved_period = st.session_state.get("period", "1y")
    default_period_idx = (
        period_options.index(_saved_period)
        if _saved_period in period_options
        else period_options.index("1y")   # safe fallback
    )
    selected_period: str = st.selectbox(
        "📅 Period",
        options=period_options,
        index=default_period_idx,
        key="detail_period_select",
    )
    st.session_state["period"] = selected_period

    # AI mode toggle
    mode_choice = st.radio(
        "⚙️ Analysis Mode",
        options=["📊 Non-AI Mode", "🤖 AI Mode"],
        index=1 if st.session_state["ai_mode"] else 0,
        key="detail_mode_radio",
    )
    st.session_state["ai_mode"] = mode_choice == "🤖 AI Mode"
    ai_mode: bool = st.session_state["ai_mode"]

    st.divider()
    load_clicked = st.button("🚀 Load Data", use_container_width=True)
    st.caption(f"Mode: {'🤖 AI' if ai_mode else '📊 Non-AI'}")


# ══════════════════════════════════════════════════════════════════════════════
# INDICATOR CALCULATIONS  (pure Pandas — no TA-Lib)
# ══════════════════════════════════════════════════════════════════════════════

def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute EMA20, EMA50, RSI-14, and daily change % using pure Pandas.

    Args:
        df: OHLCV DataFrame with DatetimeIndex and columns Open, High, Low,
            Close, Volume.

    Returns:
        Copy of df with additional columns: ema20, ema50, rsi, change_pct.
    """
    out = df.copy()

    # EMA
    out["ema20"] = out["Close"].ewm(span=20, adjust=False).mean()
    out["ema50"] = out["Close"].ewm(span=50, adjust=False).mean()

    # RSI-14 (Wilder / EWM method)
    delta = out["Close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, float("nan"))))

    # Daily change %
    out["change_pct"] = out["Close"].pct_change() * 100

    return out


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def _load_data(ticker: str, period: str) -> pd.DataFrame:
    """Load OHLCV data: try Snowflake first, fall back to yfinance.

    Args:
        ticker: Ticker symbol (e.g. "NVDA").
        period: yfinance period string used as fallback (e.g. "1y").

    Returns:
        OHLCV DataFrame with DatetimeIndex, or empty DataFrame on failure.
    """
    # Map yfinance period → approximate calendar days for Snowflake query
    period_days: Dict[str, int] = {
        "3mo": 95, "6mo": 185, "1y": 370, "3y": 1100,
    }
    days: int = period_days.get(period, 370)

    # ── Primary: Snowflake ────────────────────────────────────────────────────
    try:
        sf_df: pd.DataFrame = load_ohlcv_from_snowflake(ticker, days=days)
        if not sf_df.empty:
            sf_df = sf_df.rename(columns={
                "date": "Date", "open": "Open", "high": "High",
                "low": "Low", "close": "Close", "volume": "Volume",
            })
            sf_df = sf_df.set_index("Date")
            sf_df.index = pd.to_datetime(sf_df.index)
            log_info(f"Loaded {len(sf_df)} rows for {ticker} from Snowflake.")
            return sf_df
    except Exception as exc:
        log_warning(f"Snowflake load failed for {ticker}: {exc} — falling back to yfinance.")

    # ── Fallback: yfinance ────────────────────────────────────────────────────
    yf_df: pd.DataFrame = fetch_ohlcv(ticker, period=period)
    if not yf_df.empty:
        log_info(f"yfinance fallback successful for {ticker} ({len(yf_df)} rows).")
        # Persist to Snowflake for next time (best-effort)
        try:
            save_ohlcv_to_snowflake(yf_df, ticker)
            log_info(f"Seeded Snowflake with {len(yf_df)} rows for {ticker}.")
        except Exception as exc:
            log_warning(f"Snowflake seed failed for {ticker}: {exc}")
        return yf_df

    log_error(f"No OHLCV data available for {ticker}.")
    return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# PLOTLY CHART
# ══════════════════════════════════════════════════════════════════════════════

def _build_chart(df: pd.DataFrame, ticker: str) -> go.Figure:
    """Build the main Plotly make_subplots chart (Candlestick + Volume + RSI).

    Args:
        df:     OHLCV DataFrame with indicator columns (ema20, ema50, rsi).
        ticker: Ticker symbol used in chart titles.

    Returns:
        Plotly Figure object ready for st.plotly_chart().
    """
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.55, 0.25, 0.20],
        vertical_spacing=0.03,
    )

    # ── Row 1: Candlestick + EMA lines ───────────────────────────────────────
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name=ticker,
            increasing_line_color="#00d4aa",
            decreasing_line_color="#ef5350",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["ema20"],
            name="EMA20",
            line=dict(color="#00d4aa", width=1.5),
            opacity=0.85,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["ema50"],
            name="EMA50",
            line=dict(color="#f4c430", width=1.5, dash="dot"),
            opacity=0.85,
        ),
        row=1, col=1,
    )

    # ── Row 2: Volume bars + 20-day avg volume line ───────────────────────────
    bar_colors = [
        "#00d4aa" if c >= o else "#ef5350"
        for c, o in zip(df["Close"], df["Open"])
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            name="Volume",
            marker_color=bar_colors,
            opacity=0.8,
        ),
        row=2, col=1,
    )
    avg_vol_20d = df["Volume"].rolling(20).mean()
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=avg_vol_20d,
            name="Avg Vol (20d)",
            line=dict(color="white", width=1.2, dash="dash"),
            opacity=0.5,
        ),
        row=2, col=1,
    )

    # ── Row 3: RSI ────────────────────────────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["rsi"],
            name="RSI(14)",
            line=dict(color="#9b59b6", width=1.5),
        ),
        row=3, col=1,
    )
    for y_val, colour, dash in [
        (70, "red",   "dash"),
        (30, "green", "dash"),
        (50, "white", "dot"),
    ]:
        fig.add_hline(
            y=y_val,
            line_color=colour,
            line_dash=dash,
            opacity=0.7 if y_val != 50 else 0.3,
            row=3, col=1,
        )

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        template="plotly_dark",
        height=700,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
        xaxis_rangeslider_visible=False,
        margin=dict(l=0, r=0, t=30, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
    fig.update_yaxes(title_text="Volume",      row=2, col=1)
    fig.update_yaxes(title_text="RSI",         row=3, col=1, range=[0, 100])

    return fig


# ══════════════════════════════════════════════════════════════════════════════
# GROQ AI ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def _call_groq(summary: Dict) -> str:
    """Send ticker summary to Groq and return Thai narrative analysis.

    Args:
        summary: Dictionary of ticker KPIs and signals.

    Returns:
        Thai narrative string from the model, or an error message.
    """
    system_prompt = (
        "คุณคือนักวิเคราะห์หุ้นมืออาชีพ "
        "วิเคราะห์ข้อมูลต่อไปนี้และสรุปเป็นภาษาไทย 5-7 ประโยค "
        "ครอบคลุม: แนวโน้มราคา, สัญญาณ EMA (Bullish/Bearish), ระดับ RSI, "
        "และสิ่งที่ควรระวัง "
        "ไม่แนะนำให้ซื้อหรือขาย ไม่ต้องบอกว่าเป็น AI"
    )
    user_prompt = f"ข้อมูลหุ้น:\n{json.dumps(summary, ensure_ascii=False, indent=2)}"

    log_info(f"Groq request for {summary.get('ticker')} …")
    t0 = time.time()

    client = Groq(api_key=GROQ_API_KEY)

    # Try primary model, fall back to alternatives if deprecated
    _models_to_try = [GROQ_MODEL, "llama3-8b-8192", "llama-3.1-8b-instant", "gemma2-9b-it"]
    response = None
    last_exc = None
    for _model in _models_to_try:
        try:
            response = client.chat.completions.create(
                model=_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=512,
            )
            log_info(f"Groq used model: {_model}")
            break
        except Exception as model_exc:
            log_warning(f"Groq model {_model} failed: {model_exc}")
            last_exc = model_exc

    if response is None:
        raise last_exc or RuntimeError("All Groq models failed.")

    elapsed = round(time.time() - t0, 2)
    content: str = response.choices[0].message.content or ""
    log_info(f"Groq response for {summary.get('ticker')} received in {elapsed}s ({len(content)} chars).")
    return content


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PAGE RENDER
# ══════════════════════════════════════════════════════════════════════════════

st.title(f"🕯️ {selected_ticker} — Stock Detail")
ticker_meta = TICKER_INFO.get(selected_ticker, {})
st.caption(
    f"{ticker_meta.get('name', selected_ticker)}  ·  "
    f"{ticker_meta.get('sector', '')}  ·  "
    f"Period: {selected_period}  ·  "
    f"Mode: {'🤖 AI' if ai_mode else '📊 Non-AI'}"
)
st.divider()

# ── Load data on button click or first render ─────────────────────────────────
if load_clicked or "detail_df" not in st.session_state or \
        st.session_state.get("detail_ticker") != selected_ticker or \
        st.session_state.get("detail_period") != selected_period:

    with st.spinner(f"กำลังโหลดข้อมูล {selected_ticker} ..."):
        raw_df: pd.DataFrame = _load_data(selected_ticker, selected_period)

    if raw_df.empty:
        st.error(f"⚠️ ไม่พบข้อมูลสำหรับ {selected_ticker} กรุณาลองใหม่")
        st.stop()

    st.session_state["detail_df"]     = _add_indicators(raw_df)
    st.session_state["detail_ticker"] = selected_ticker
    st.session_state["detail_period"] = selected_period

df: pd.DataFrame = st.session_state.get("detail_df", pd.DataFrame())

if df.empty:
    st.info("👈 กด **Load Data** เพื่อเริ่มต้น")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — KPI ROW
# ══════════════════════════════════════════════════════════════════════════════

kpi: Dict = get_kpi_stats(df)

current_price = kpi.get("current_price")
return_30d    = kpi.get("return_30d")
avg_vol_30d   = kpi.get("avg_volume_30d")
rsi_val       = kpi.get("rsi_current")

# Previous close delta for price metric
prev_close: float | None = None
if len(df) >= 2:
    prev_close = round(float(df["Close"].iloc[-2]), 4)

price_delta = (
    f"{current_price - prev_close:+.2f} vs prev close"
    if current_price and prev_close else None
)

# Volume formatted as "12.3M" / "1.2B"
def _fmt_volume(v: int | None) -> str:
    """Format volume as human-readable string (K / M / B).

    Args:
        v: Raw volume integer.

    Returns:
        Formatted string such as "45.2M" or "N/A".
    """
    if v is None:
        return "N/A"
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return str(v)

# RSI signal label
def _rsi_label(rsi: float | None) -> str:
    """Return RSI zone label.

    Args:
        rsi: Current RSI value.

    Returns:
        "Overbought >70", "Oversold <30", or "Neutral".
    """
    if rsi is None:
        return "N/A"
    if rsi > 70:
        return "Overbought >70 🔴"
    if rsi < 30:
        return "Oversold <30 🟢"
    return "Neutral ⚪"

col_k1, col_k2, col_k3, col_k4 = st.columns(4)

with col_k1:
    st.metric(
        label="💵 Current Price",
        value=f"${current_price:.2f}" if current_price else "N/A",
        delta=price_delta,
    )
with col_k2:
    st.metric(
        label="📈 30-Day Return",
        value=f"{return_30d:+.2f}%" if return_30d is not None else "N/A",
        delta="vs 30 trading days ago",
    )
with col_k3:
    st.metric(
        label="📦 Avg Daily Volume (30d)",
        value=_fmt_volume(avg_vol_30d),
    )
with col_k4:
    st.metric(
        label="📉 RSI(14)",
        value=f"{rsi_val:.1f}" if rsi_val is not None else "N/A",
        delta=_rsi_label(rsi_val),
    )

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — PLOTLY CHART (shown in both modes)
# ══════════════════════════════════════════════════════════════════════════════

st.subheader(f"📊 {selected_ticker} — Candlestick · Volume · RSI")
fig = _build_chart(df, selected_ticker)
st.plotly_chart(fig, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — OHLCV DATA TABLE
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("📋 ดูข้อมูล OHLCV (30 วันล่าสุด)"):
    table_cols = ["Open", "High", "Low", "Close", "Volume", "ema20", "ema50", "rsi"]
    display_table = df[table_cols].tail(30).copy()
    display_table.index.name = "Date"

    st.dataframe(
        display_table.style.format({
            "Open":   "${:.2f}",
            "High":   "${:.2f}",
            "Low":    "${:.2f}",
            "Close":  "${:.2f}",
            "Volume": "{:,.0f}",
            "ema20":  "${:.2f}",
            "ema50":  "${:.2f}",
            "rsi":    "{:.1f}",
        }),
        use_container_width=True,
    )

# ══════════════════════════════════════════════════════════════════════════════
# AI MODE — Groq Analysis
# ══════════════════════════════════════════════════════════════════════════════

if ai_mode:
    st.divider()
    st.subheader("🤖 AI Analysis")

    # Build summary dict for the prompt
    latest = df.iloc[-1]
    ema20_last  = float(latest["ema20"])  if pd.notna(latest["ema20"]) else None
    ema50_last  = float(latest["ema50"])  if pd.notna(latest["ema50"]) else None
    rsi_last    = float(latest["rsi"])    if pd.notna(latest["rsi"])   else None

    ema_signal: str = "Bullish" if (ema20_last and ema50_last and ema20_last > ema50_last) else "Bearish"
    price_vs_ema20: float | None = (
        round((float(latest["Close"]) / ema20_last - 1) * 100, 2)
        if ema20_last else None
    )
    rsi_signal: str
    if rsi_last is None:
        rsi_signal = "N/A"
    elif rsi_last > 70:
        rsi_signal = "Overbought"
    elif rsi_last < 30:
        rsi_signal = "Oversold"
    else:
        rsi_signal = "Neutral"

    summary: Dict = {
        "ticker":             selected_ticker,
        "company":            ticker_meta.get("name", selected_ticker),
        "sector":             ticker_meta.get("sector", ""),
        "period":             selected_period,
        "current_price":      current_price,
        "return_30d_pct":     return_30d,
        "avg_volume_30d":     avg_vol_30d,
        "rsi_current":        round(rsi_last, 2) if rsi_last else None,
        "rsi_signal":         rsi_signal,
        "ema_signal":         ema_signal,
         "price_vs_ema20_pct": price_vs_ema20,
    }

    with st.spinner("AI กำลังวิเคราะห์..."):
        try:
            ai_response: str = _call_groq(summary)
            st.info(ai_response)
        except Exception as exc:
            log_error(f"Groq call failed for {selected_ticker}: {exc}")
            st.error(f"❌ ไม่สามารถเชื่อมต่อ AI ได้: {exc}")

    st.caption("⚠️ เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน")
