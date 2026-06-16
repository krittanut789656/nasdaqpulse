"""
config/settings.py
==================
Global configuration for NasdaqPulse.

Credential loading priority:
  1. st.secrets  (used when running via Streamlit)
  2. os.environ / .env file  (used for scripts & local dev)
"""

import os
from typing import Dict, List

# ── Credential Loading ────────────────────────────────────────────────────────
try:
    import streamlit as st

    MONGODB_URI: str = st.secrets["mongodb"]["uri"]
    MONGODB_DATABASE: str = st.secrets["mongodb"]["database"]

    SNOWFLAKE_ACCOUNT: str = st.secrets["snowflake"]["account"]
    SNOWFLAKE_USER: str = st.secrets["snowflake"]["user"]
    SNOWFLAKE_PASSWORD: str = st.secrets["snowflake"]["password"]
    SNOWFLAKE_DATABASE: str = st.secrets["snowflake"]["database"]
    SNOWFLAKE_SCHEMA: str = st.secrets["snowflake"]["schema"]
    SNOWFLAKE_WAREHOUSE: str = st.secrets["snowflake"]["warehouse"]

    GROQ_API_KEY: str = st.secrets["groq"]["api_key"]

except Exception:
    from dotenv import load_dotenv

    load_dotenv()

    MONGODB_URI = os.getenv("MONGODB_URI", "")
    MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "stock_db")

    SNOWFLAKE_ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT", "mj18661")
    SNOWFLAKE_USER = os.getenv("SNOWFLAKE_USER", "")
    SNOWFLAKE_PASSWORD = os.getenv("SNOWFLAKE_PASSWORD", "")
    SNOWFLAKE_DATABASE = os.getenv("SNOWFLAKE_DATABASE", "NASDAQPULSE")
    SNOWFLAKE_SCHEMA = os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC")
    SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")

    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


# ── Tickers ───────────────────────────────────────────────────────────────────
NASDAQ_100_TICKERS: List[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META",
    "GOOGL", "TSLA", "AVGO", "COST", "ASML",
]

# ── Ticker Metadata ───────────────────────────────────────────────────────────
TICKER_INFO: Dict[str, Dict[str, str]] = {
    "AAPL":  {"name": "Apple Inc.",              "sector": "Technology"},
    "MSFT":  {"name": "Microsoft Corporation",   "sector": "Technology"},
    "NVDA":  {"name": "NVIDIA Corporation",      "sector": "Technology"},
    "AMZN":  {"name": "Amazon.com Inc.",         "sector": "Consumer Discretionary"},
    "META":  {"name": "Meta Platforms Inc.",     "sector": "Technology"},
    "GOOGL": {"name": "Alphabet Inc.",           "sector": "Technology"},
    "TSLA":  {"name": "Tesla Inc.",              "sector": "Consumer Discretionary"},
    "AVGO":  {"name": "Broadcom Inc.",           "sector": "Technology"},
    "COST":  {"name": "Costco Wholesale Corp.",  "sector": "Consumer Staples"},
    "ASML":  {"name": "ASML Holding N.V.",       "sector": "Technology"},
}

# ── Data Defaults ─────────────────────────────────────────────────────────────
DEFAULT_PERIOD: str = "1y"
DEFAULT_INTERVAL: str = "1d"

# ── MongoDB Collections ───────────────────────────────────────────────────────
MONGO_COLLECTION_META: str = "stock_metadata"
MONGO_COLLECTION_NEWS: str = "stock_news"

# ── Snowflake Table ───────────────────────────────────────────────────────────
SNOWFLAKE_TABLE_OHLCV: str = "ohlcv"

# ── Groq ──────────────────────────────────────────────────────────────────────
GROQ_MODEL: str = "llama3-8b-8192"          # fallback-safe; was llama-3.1-8b-instant
