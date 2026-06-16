"""
app.py
======
NasdaqPulse — Streamlit multi-page entry point.

Run with:
    streamlit run app.py

Pages are auto-discovered from the pages/ folder by Streamlit.
This file handles:
  - Global page config
  - Session state initialisation
  - Shared sidebar (ticker selector, AI mode toggle, refresh button)
  - Custom CSS theming
"""

import streamlit as st

# ── Page config (must be the very first Streamlit call) ───────────────────────
st.set_page_config(
    page_title="NasdaqPulse",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
        /* Base background */
        .stApp {
            background-color: #0e1117;
            color: #fafafa;
        }

        /* Sidebar */
        section[data-testid="stSidebar"] {
            background-color: #161b22;
        }

        /* Accent colour for metric values and highlights */
        .highlight {
            color: #00d4aa;
            font-weight: 700;
        }

        /* Card-style containers */
        div[data-testid="stMetric"] {
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 12px 16px;
        }

        /* Primary button */
        .stButton > button {
            background-color: #00d4aa;
            color: #0e1117;
            font-weight: 600;
            border: none;
            border-radius: 6px;
        }
        .stButton > button:hover {
            background-color: #00b894;
            color: #0e1117;
        }

        /* Selectbox / radio accent */
        .stSelectbox label, .stRadio label {
            color: #8b949e;
            font-size: 0.85rem;
        }

        /* Divider */
        hr {
            border-color: #30363d;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session state initialisation ──────────────────────────────────────────────
_DEFAULTS: dict = {
    "selected_ticker": "NVDA",
    "ai_mode": False,
    "period": "1y",
    "chat_history": [],
}

for key, value in _DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📈 NasdaqPulse")
    st.caption("Nasdaq 100 · DADS5001 Final Project")
    st.divider()

    # Ticker selector
    from config.settings import NASDAQ_100_TICKERS, TICKER_INFO

    ticker_labels = [
        f"{t} — {TICKER_INFO[t]['name']}" for t in NASDAQ_100_TICKERS
    ]
    selected_label = st.selectbox(
        "🔍 Select Ticker",
        options=ticker_labels,
        index=NASDAQ_100_TICKERS.index(st.session_state["selected_ticker"]),
    )
    st.session_state["selected_ticker"] = selected_label.split(" — ")[0]

    # Period selector
    _valid_periods = ["1mo", "3mo", "6mo", "1y", "2y", "5y"]
    if st.session_state["period"] not in _valid_periods:
        st.session_state["period"] = "1y"
    st.session_state["period"] = st.selectbox(
        "📅 Period",
        options=_valid_periods,
        index=_valid_periods.index(st.session_state["period"]),
    )

    # AI mode toggle
    st.session_state["ai_mode"] = st.toggle(
        "🤖 AI Mode",
        value=st.session_state["ai_mode"],
    )

    st.divider()

    # Status badges
    ticker = st.session_state["selected_ticker"]
    info = TICKER_INFO[ticker]
    st.markdown(f"**Ticker:** <span class='highlight'>{ticker}</span>", unsafe_allow_html=True)
    st.markdown(f"**Company:** {info['name']}")
    st.markdown(f"**Sector:** {info['sector']}")
    st.markdown(
        f"**AI Mode:** {'<span class=\"highlight\">ON 🟢</span>' if st.session_state['ai_mode'] else 'OFF 🔴'}",
        unsafe_allow_html=True,
    )

    st.divider()

    # Refresh button
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.caption("Data · Yahoo Finance · MongoDB · Snowflake")

# ── Landing page content ──────────────────────────────────────────────────────
st.markdown("# 📈 NasdaqPulse")
st.markdown(
    "**Nasdaq 100 Stock Market Analysis Dashboard** · DADS5001 Final Project"
)
st.divider()

col1, col2, col3 = st.columns(3)
with col1:
    st.info("### 📊 Overview\nNasdaq 100 summary table, top gainers & losers, sector heatmap.")
with col2:
    st.info("### 🕯️ Stock Detail\nCandlestick · Volume · RSI chart — or AI summary in AI Mode.")
with col3:
    st.info("### 🤖 AI Analyst\nRAG-powered Thai narrative insights via Groq llama-3.1-8b-instant.")

st.divider()
st.markdown(
    "👈 **Select a page from the sidebar** to begin, "
    "or use the ticker selector and period controls above."
)
