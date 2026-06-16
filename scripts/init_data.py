"""
scripts/init_data.py
====================
NasdaqPulse — One-time data initialisation script.

Run ONCE before starting the Streamlit app:
    python scripts/init_data.py

Steps:
  1. Connect to MongoDB and Snowflake
  2. Create MongoDB text index on tickers collection
  3. Save ticker metadata to MongoDB
  4. Create Snowflake OHLCV table
  5. Fetch 3-year OHLCV from yfinance for all 10 tickers
  6. Run data quality check per ticker
  7. Save valid OHLCV data to Snowflake
  8. Print summary (rows saved, time taken, failed tickers)
"""

from __future__ import annotations

import sys
import time
import datetime
import os
from pathlib import Path
from typing import Dict, List

# ── Make project root importable (scripts/ lives one level below root) ────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

# ── Project imports (after sys.path fix) ─────────────────────────────────────
from config.settings import NASDAQ_100_TICKERS, TICKER_INFO, SNOWFLAKE_TABLE_OHLCV
from utils.logger import log_info, log_error, log_warning
from utils.data_quality import generate_quality_report


# ══════════════════════════════════════════════════════════════════════════════
# FORMATTING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _banner(text: str) -> None:
    """Print a section banner to stdout.

    Args:
        text: Banner text to display.
    """
    width = 60
    print("\n" + "═" * width)
    print(f"  {text}")
    print("═" * width)


def _ok(msg: str) -> None:
    """Print a success line.

    Args:
        msg: Message to display after the ✅ prefix.
    """
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    """Print a failure line.

    Args:
        msg: Message to display after the ❌ prefix.
    """
    print(f"  ❌ {msg}")


def _info(msg: str) -> None:
    """Print an informational line.

    Args:
        msg: Message to display after the ℹ️  prefix.
    """
    print(f"  ℹ️  {msg}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — CONNECTIONS
# ══════════════════════════════════════════════════════════════════════════════

def step1_connect() -> tuple:
    """Connect to MongoDB and Snowflake and validate both connections.

    Returns:
        Tuple of (mongo_client, snowflake_conn).

    Raises:
        SystemExit: If either connection fails.
    """
    _banner("STEP 1 — Connecting to databases")

    # MongoDB
    mongo_client = None
    try:
        from pymongo import MongoClient
        from config.settings import MONGODB_URI, MONGODB_DATABASE
        mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10_000)
        mongo_client.admin.command("ping")
        _ok(f"Connected to MongoDB (database={MONGODB_DATABASE})")
        log_info("MongoDB connection verified.")
    except Exception as exc:
        _fail(f"MongoDB connection failed: {exc}")
        log_error(f"MongoDB connection failed: {exc}")
        sys.exit(1)

    # Snowflake
    sf_conn = None
    try:
        import snowflake.connector
        from config.settings import (
            SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
            SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE,
        )
        sf_conn = snowflake.connector.connect(
            account=SNOWFLAKE_ACCOUNT,
            user=SNOWFLAKE_USER,
            password=SNOWFLAKE_PASSWORD,
            database=SNOWFLAKE_DATABASE,
            schema=SNOWFLAKE_SCHEMA,
            warehouse=SNOWFLAKE_WAREHOUSE,
            login_timeout=30,
        )
        _ok(f"Connected to Snowflake (account={SNOWFLAKE_ACCOUNT}, db={SNOWFLAKE_DATABASE})")
        log_info("Snowflake connection verified.")
    except Exception as exc:
        _fail(f"Snowflake connection failed: {exc}")
        log_error(f"Snowflake connection failed: {exc}")
        sys.exit(1)

    return mongo_client, sf_conn


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — MONGODB TEXT INDEX
# ══════════════════════════════════════════════════════════════════════════════

def step2_create_mongo_index(mongo_client) -> None:
    """Create a MongoDB text index on ticker, name, and sector fields.

    Args:
        mongo_client: Active PyMongo MongoClient.
    """
    _banner("STEP 2 — Creating MongoDB text index")
    try:
        from config.settings import MONGODB_DATABASE
        from pymongo import TEXT
        db = mongo_client[MONGODB_DATABASE]
        col = db["tickers"]
        col.create_index(
            [("ticker", TEXT), ("name", TEXT), ("sector", TEXT)],
            name="ticker_text_idx",
            default_language="english",
        )
        _ok("MongoDB text index created on tickers (ticker, name, sector)")
        log_info("MongoDB text index ensured.")
    except Exception as exc:
        _fail(f"MongoDB text index creation failed: {exc}")
        log_error(f"MongoDB text index failed: {exc}")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — SAVE TICKER METADATA TO MONGODB
# ══════════════════════════════════════════════════════════════════════════════

def step3_save_metadata(mongo_client) -> None:
    """Upsert ticker metadata documents into the MongoDB tickers collection.

    Args:
        mongo_client: Active PyMongo MongoClient.
    """
    _banner("STEP 3 — Saving ticker metadata to MongoDB")
    try:
        from config.settings import MONGODB_DATABASE
        db = mongo_client[MONGODB_DATABASE]
        col = db["tickers"]
        saved = 0
        for ticker, info in TICKER_INFO.items():
            doc = {
                "ticker":       ticker,
                "name":         info["name"],
                "sector":       info["sector"],
                "last_updated": datetime.datetime.utcnow().isoformat(),
                "latest_close": None,
            }
            col.replace_one({"ticker": ticker}, doc, upsert=True)
            saved += 1
            _info(f"Upserted {ticker} — {info['name']} ({info['sector']})")

        _ok(f"Saved {saved} tickers to MongoDB (collection: tickers)")
        log_info(f"Upserted {saved} ticker metadata documents.")
    except Exception as exc:
        _fail(f"MongoDB metadata save failed: {exc}")
        log_error(f"MongoDB metadata save failed: {exc}")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — CREATE SNOWFLAKE OHLCV TABLE
# ══════════════════════════════════════════════════════════════════════════════

def step4_create_snowflake_table(sf_conn) -> None:
    """Create the OHLCV history table in Snowflake if it does not exist.

    Args:
        sf_conn: Active Snowflake connection.
    """
    _banner("STEP 4 — Creating Snowflake OHLCV table")
    ddl = f"""
        CREATE TABLE IF NOT EXISTS {SNOWFLAKE_TABLE_OHLCV} (
            ticker  VARCHAR(10)  NOT NULL,
            date    DATE         NOT NULL,
            open    FLOAT,
            high    FLOAT,
            low     FLOAT,
            close   FLOAT,
            volume  BIGINT,
            PRIMARY KEY (ticker, date)
        )
    """
    try:
        from config.settings import SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE
        with sf_conn.cursor() as cur:
            cur.execute(f"CREATE WAREHOUSE IF NOT EXISTS {SNOWFLAKE_WAREHOUSE} WITH WAREHOUSE_SIZE='X-SMALL' AUTO_SUSPEND=60 AUTO_RESUME=TRUE")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {SNOWFLAKE_DATABASE}")
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SNOWFLAKE_DATABASE}.{SNOWFLAKE_SCHEMA}")
            cur.execute(f"USE WAREHOUSE {SNOWFLAKE_WAREHOUSE}")
            cur.execute(f"USE DATABASE {SNOWFLAKE_DATABASE}")
            cur.execute(f"USE SCHEMA {SNOWFLAKE_SCHEMA}")
            cur.execute(ddl)
        _ok(f"Snowflake table '{SNOWFLAKE_TABLE_OHLCV}' ready (CREATE IF NOT EXISTS)")
        log_info("Snowflake OHLCV table ensured.")
    except Exception as exc:
        _fail(f"Snowflake table creation failed: {exc}")
        log_error(f"Snowflake DDL failed: {exc}")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — FETCH OHLCV FROM YFINANCE
# ══════════════════════════════════════════════════════════════════════════════

def step5_fetch_ohlcv() -> Dict[str, pd.DataFrame]:
    """Fetch 3-year daily OHLCV data from Yahoo Finance for all tickers.

    Runs a data quality report per ticker.
    Tickers with invalid schema are skipped.

    Returns:
        Dictionary mapping ticker → valid OHLCV DataFrame.
    """
    _banner("STEP 5 — Fetching 3-year OHLCV from Yahoo Finance")
    import yfinance as yf

    valid_data: Dict[str, pd.DataFrame] = {}
    failed: List[str] = []

    for ticker in NASDAQ_100_TICKERS:
        print(f"\n  📥 Fetching {ticker} ...")
        try:
            raw: pd.DataFrame = yf.download(
                ticker,
                period="3y",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
            )

            if raw.empty:
                _fail(f"{ticker}: yfinance returned empty DataFrame — skipping.")
                log_error(f"{ticker}: empty yfinance response.")
                failed.append(ticker)
                continue

            # Flatten MultiIndex columns (yfinance ≥ 0.2.x)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)

            cols_wanted = ["Open", "High", "Low", "Close", "Volume"]
            df = raw[[c for c in cols_wanted if c in raw.columns]].copy()
            df.index.name = "Date"

            # Quality report
            report = generate_quality_report(df, ticker)
            score = report["quality_score"]
            colour = "🟢" if score >= 90 else ("🟡" if score >= 70 else "🔴")
            print(f"     Quality Score: {colour} {score:.1f}/100  |  Rows: {report['total_rows']}")

            if not report["schema_valid"]:
                _fail(f"{ticker}: schema invalid — skipping.")
                log_error(f"{ticker}: schema invalid, skipped.")
                failed.append(ticker)
                continue

            valid_data[ticker] = df
            _ok(f"{ticker}: {report['total_rows']} rows fetched (quality {score:.1f}/100)")

        except Exception as exc:
            _fail(f"{ticker}: fetch error — {exc}")
            log_error(f"{ticker}: yfinance fetch failed: {exc}")
            failed.append(ticker)

    print(f"\n  📊 Fetched {len(valid_data)}/{len(NASDAQ_100_TICKERS)} tickers successfully.")
    if failed:
        print(f"  ⚠️  Failed tickers: {failed}")

    return valid_data


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — SAVE TO SNOWFLAKE
# ══════════════════════════════════════════════════════════════════════════════

def step6_save_to_snowflake(sf_conn, valid_data: Dict[str, pd.DataFrame]) -> Dict[str, int]:
    """Delete and re-insert OHLCV rows for each ticker into Snowflake.

    Args:
        sf_conn:    Active Snowflake connection.
        valid_data: Mapping of ticker → validated OHLCV DataFrame.

    Returns:
        Dictionary mapping ticker → number of rows saved.
    """
    _banner("STEP 6 — Saving OHLCV data to Snowflake")
    rows_saved: Dict[str, int] = {}
    failed: List[str] = []

    for ticker, df in valid_data.items():
        try:
            from config.settings import SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE
            with sf_conn.cursor() as _cur:
                _cur.execute(f"USE WAREHOUSE {SNOWFLAKE_WAREHOUSE}")
                _cur.execute(f"USE DATABASE {SNOWFLAKE_DATABASE}")
                _cur.execute(f"USE SCHEMA {SNOWFLAKE_SCHEMA}")
            data = df.copy().reset_index()
            data = data.rename(columns={
                "Date": "date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume",
            })
            data["ticker"] = ticker
            data["date"] = pd.to_datetime(data["date"]).dt.date
            rows = data[["ticker", "date", "open", "high", "low", "close", "volume"]].values.tolist()

            with sf_conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {SNOWFLAKE_TABLE_OHLCV} WHERE ticker = %s",
                    (ticker,),
                )
                cur.executemany(
                    f"""
                    INSERT INTO {SNOWFLAKE_TABLE_OHLCV}
                        (ticker, date, open, high, low, close, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )

            rows_saved[ticker] = len(rows)
            _ok(f"Saved {len(rows):,} rows for {ticker} to Snowflake")
            log_info(f"Snowflake: saved {len(rows)} rows for {ticker}.")

        except Exception as exc:
            _fail(f"{ticker}: Snowflake insert failed — {exc}")
            log_error(f"Snowflake insert failed for {ticker}: {exc}")
            failed.append(ticker)

    if failed:
        print(f"\n  ⚠️  Snowflake insert failed for: {failed}")

    return rows_saved


# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def step7_summary(
    rows_saved: Dict[str, int],
    valid_data: Dict[str, pd.DataFrame],
    elapsed: float,
) -> None:
    """Print a final summary of the initialisation run.

    Args:
        rows_saved:  Mapping of ticker → rows inserted into Snowflake.
        valid_data:  Mapping of ticker → fetched DataFrame (used to compute failed list).
        elapsed:     Total wall-clock time in seconds.
    """
    _banner("STEP 7 — Summary")
    total_rows = sum(rows_saved.values())
    failed_tickers = [t for t in NASDAQ_100_TICKERS if t not in rows_saved]

    print(f"  ⏱️  Total time     : {elapsed:.1f} seconds")
    print(f"  📦 Total rows saved: {total_rows:,}")
    print(f"  ✅ Tickers done    : {list(rows_saved.keys())}")
    if failed_tickers:
        print(f"  ❌ Tickers failed  : {failed_tickers}")
    else:
        print("  🎉 All tickers initialised successfully!")

    print("\n  ▶️  You can now run the app with:")
    print("       streamlit run app.py\n")
    log_info(f"init_data.py complete. {total_rows} rows saved in {elapsed:.1f}s. Failed: {failed_tickers}")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Run all initialisation steps in sequence."""
    print("\n" + "█" * 60)
    print("  NasdaqPulse — Data Initialisation Script")
    print("  " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("█" * 60)

    t_start = time.time()

    # Step 1
    mongo_client, sf_conn = step1_connect()

    # Step 2
    try:
        step2_create_mongo_index(mongo_client)
    except Exception:
        _fail("Skipping MongoDB index step due to error.")

    # Step 3
    try:
        step3_save_metadata(mongo_client)
    except Exception:
        _fail("Skipping MongoDB metadata step due to error.")

    # Step 4
    try:
        step4_create_snowflake_table(sf_conn)
    except Exception:
        _fail("Skipping Snowflake table creation due to error.")
        sys.exit(1)

    # Step 5
    valid_data: Dict[str, pd.DataFrame] = step5_fetch_ohlcv()

    if not valid_data:
        _fail("No valid OHLCV data fetched — aborting Snowflake insert.")
        log_error("init_data.py: no valid data to insert.")
        sys.exit(1)

    # Step 6
    rows_saved: Dict[str, int] = step6_save_to_snowflake(sf_conn, valid_data)

    # Step 7
    elapsed = round(time.time() - t_start, 1)
    step7_summary(rows_saved, valid_data, elapsed)

    # Cleanup
    try:
        sf_conn.close()
        mongo_client.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
