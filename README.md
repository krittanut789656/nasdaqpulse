# 📈 NasdaqPulse

**Nasdaq 100 Stock Market Analysis Dashboard with AI Analyst**
Built with Streamlit, DuckDB, MongoDB, Snowflake, yfinance, Groq

DADS5001 — Final Project · Krittanut · 2026

---

## Overview

NasdaqPulse is a multi-page Streamlit dashboard for analyzing the top 10 representative Nasdaq 100 stocks.
It combines real-time data from Yahoo Finance, cloud storage on MongoDB Atlas & Snowflake,
in-memory SQL via DuckDB, and a Thai-language AI analyst powered by Groq (llama3-8b-8192) with RAG.

---

## Pages

| Page | Mode | Description |
|------|------|-------------|
| 📊 Overview | — | Nasdaq summary table, top 5 gainers/losers bar chart, sector treemap heatmap, data quality report |
| 🕯️ Stock Detail | Non-AI | Candlestick + Volume + RSI(14) Plotly dashboard, 4 KPI cards |
| 🕯️ Stock Detail | AI | Same chart + Groq Thai narrative analysis |
| 🤖 AI Analyst | RAG | MongoDB text search → live snapshot enrichment → Groq Thai answer, chat history |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Streamlit (multi-page, session_state, cache) |
| Data Fetch | yfinance (auto_adjust=True, ttl=3600) |
| Processing | Pandas, DuckDB (in-memory SQL) |
| Cloud DB 1 | MongoDB Atlas — ticker metadata + RAG text search |
| Cloud DB 2 | Snowflake — OHLCV history table |
| Visualization | Plotly (make_subplots: Candlestick + Volume + RSI) |
| AI / LLM | Groq API — llama3-8b-8192 with fallback list (RAG, Thai output) |
| Logging | Python standard logging → logs/app.log |

---

## Tickers

`AAPL` `MSFT` `NVDA` `AMZN` `META` `GOOGL` `TSLA` `AVGO` `COST` `ASML`

---

## Data Architecture

```
yfinance → Data Quality Check (data_quality.py)
              ├── MongoDB Atlas  (stock_metadata — RAG context)
              └── Snowflake      (ohlcv — OHLCV history)

DuckDB (in-memory) — SQL over Pandas DataFrames
  ├── get_top_movers()
  ├── get_sector_summary()
  └── get_kpi_stats() + RSI-14 (pure Pandas, no TA-Lib)

RAG Pipeline (AI Analyst page):
  MongoDB $text search → live yfinance snapshot → Groq → Thai response
```

---

## Caching Strategy

| Function | Decorator | TTL |
|----------|-----------|-----|
| `get_mongo_client()` | `st.cache_resource` | session |
| `get_snowflake_connection()` | `st.cache_resource` | session |
| `fetch_ohlcv()` | `st.cache_data` | 1 hour |
| `load_ohlcv_from_snowflake()` | `st.cache_data` | 1 hour |
| `get_latest_snapshot()` | `st.cache_data` | 15 min |
| `load_ticker_metadata()` | `st.cache_data` | 5 min |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Fill in credentials

```bash
cp .env.example .env
# Edit .env with your values:
#   SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, GROQ_API_KEY
```

Also edit `.streamlit/secrets.toml` with the same credentials (used when running via Streamlit).

### 3. Initialise databases (run once)

```bash
python scripts/init_data.py
```

This will:
- Connect to MongoDB and Snowflake
- Create MongoDB text index on tickers collection
- Seed ticker metadata to MongoDB
- Create Snowflake `ohlcv` table
- Fetch 3 years of OHLCV data for all 10 tickers
- Run data quality check per ticker
- Save all valid OHLCV data to Snowflake

### 4. Run the app

```bash
streamlit run app.py
```

---

## Deployment (Streamlit Community Cloud)

1. Push this repository to GitHub (ensure `.env` and `secrets.toml` are in `.gitignore`)
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
3. Select your repo, branch, and set **Main file path** to `app.py`
4. Open **App Settings → Secrets** and paste the contents of your `secrets.toml`:

```toml
[mongodb]
uri = "mongodb+srv://..."
database = "stock_db"

[snowflake]
account = "mj18661.ap-southeast-7.aws"
user = "YOUR_USER"
password = "YOUR_PASSWORD"
database = "NASDAQPULSE"
schema = "PUBLIC"
warehouse = "COMPUTE_WH"

[groq]
api_key = "YOUR_GROQ_API_KEY"
```

5. Deploy — Streamlit Cloud reads secrets from the dashboard, not from the repo.

---

## Project Structure

```
NasdaqPulse_Project/
├── app.py                    # Entry point: page config, session_state, sidebar, CSS
├── requirements.txt          # Python dependencies
├── .env.example              # Credential template
├── .gitignore                # Excludes .env, secrets.toml, __pycache__, logs/
├── .streamlit/
│   ├── config.toml           # Dark theme (#0e1117 bg, #00d4aa accent)
│   └── secrets.toml          # Credentials (NOT committed to git)
├── config/
│   ├── settings.py           # Credential loading (st.secrets → .env fallback)
│   └── connections.py        # Reserved for future connection helpers
├── utils/
│   ├── logger.py             # File + console logging (logs/app.log)
│   ├── data_quality.py       # OHLCV validation + quality score (0-100)
│   ├── mongodb_client.py     # MongoDB CRUD + $text search + cache
│   ├── snowflake_client.py   # Snowflake DDL + OHLCV read/write + cache
│   ├── yfinance_loader.py    # yfinance fetch + quality check + cache
│   └── duckdb_engine.py      # In-memory SQL: movers, sector, KPIs, RSI
├── pages/
│   ├── 1_Overview.py         # Nasdaq table, gainers/losers chart, sector treemap
│   ├── 2_Stock_Detail.py     # Candlestick+Volume+RSI + Groq AI mode
│   └── 3_AI_Analyst.py       # RAG pipeline + chat history
├── scripts/
│   └── init_data.py          # One-time DB init + 3y OHLCV seed
└── logs/
    └── app.log               # Runtime logs (auto-created)
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MONGODB_URI` | MongoDB Atlas connection string |
| `MONGODB_DATABASE` | Database name (`stock_db`) |
| `SNOWFLAKE_ACCOUNT` | Snowflake account identifier (format: `locator.region.aws`) |
| `SNOWFLAKE_USER` | **Fill in** |
| `SNOWFLAKE_PASSWORD` | **Fill in** |
| `SNOWFLAKE_DATABASE` | `NASDAQPULSE` |
| `SNOWFLAKE_SCHEMA` | `PUBLIC` |
| `SNOWFLAKE_WAREHOUSE` | `COMPUTE_WH` |
| `GROQ_API_KEY` | **Fill in** — get from console.groq.com |

---

## AI Disclaimer

> ⚠️ NasdaqPulse AI Analyst is for **educational purposes only**.
> It does not provide investment advice. Always consult a licensed financial