"""
utils/snowflake_client.py
=========================
Snowflake helpers for NasdaqPulse.

Table: NASDAQPULSE.PUBLIC.ohlcv
  ticker   VARCHAR
  date     DATE
  open     FLOAT
  high     FLOAT
  low      FLOAT
  close    FLOAT
  volume   BIGINT

Connection is cached with st.cache_resource.
All heavy reads are cached with st.cache_data.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
import snowflake.connector
from snowflake.connector import SnowflakeConnection

from config.settings import (
    SNOWFLAKE_ACCOUNT,
    SNOWFLAKE_USER,
    SNOWFLAKE_PASSWORD,
    SNOWFLAKE_DATABASE,
    SNOWFLAKE_SCHEMA,
    SNOWFLAKE_WAREHOUSE,
    SNOWFLAKE_TABLE_OHLCV,
)
from utils.logger import log_info, log_error


# ── Connection ────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def get_snowflake_connection() -> SnowflakeConnection:
    """Create and cache a Snowflake connection using credentials from settings.

    Uses st.cache_resource so the connection object is reused across reruns.

    Returns:
        An active SnowflakeConnection.

    Raises:
        ConnectionError: If the connection attempt fails.
    """
    log_info(
        f"Connecting to Snowflake (account={SNOWFLAKE_ACCOUNT}, "
        f"db={SNOWFLAKE_DATABASE}, schema={SNOWFLAKE_SCHEMA}) …"
    )
    try:
        conn: SnowflakeConnection = snowflake.connector.connect(
            account=SNOWFLAKE_ACCOUNT,
            user=SNOWFLAKE_USER,
            password=SNOWFLAKE_PASSWORD,
            database=SNOWFLAKE_DATABASE,
            schema=SNOWFLAKE_SCHEMA,
            warehouse=SNOWFLAKE_WAREHOUSE,
            login_timeout=30,
        )
        log_info("Snowflake connection established.")
        return conn
    except Exception as exc:
        log_error(f"Snowflake connection failed: {exc}")
        raise ConnectionError(f"Snowflake connection failed: {exc}") from exc


# ── DDL ───────────────────────────────────────────────────────────────────────

def create_ohlcv_table() -> None:
    """Create the OHLCV history table in Snowflake if it does not exist.

    Safe to call multiple times (uses CREATE TABLE IF NOT EXISTS).
    """
    ddl: str = f"""
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
        conn: SnowflakeConnection = get_snowflake_connection()
        with conn.cursor() as cur:
            cur.execute(ddl)
        log_info(f"Table '{SNOWFLAKE_TABLE_OHLCV}' ensured in Snowflake.")
    except Exception as exc:
        log_error(f"Failed to create OHLCV table: {exc}")
        raise


# ── Write ─────────────────────────────────────────────────────────────────────

def save_ohlcv_to_snowflake(df: pd.DataFrame, ticker: str) -> None:
    """Delete existing rows for a ticker and insert fresh OHLCV data.

    Strategy: DELETE + bulk INSERT ensures no duplicates and keeps the
    table current without requiring a MERGE statement.

    Args:
        df:     OHLCV DataFrame with columns Open, High, Low, Close, Volume
                and a DatetimeIndex (or a 'Date' column).
        ticker: Ticker symbol (e.g. "NVDA").
    """
    try:
        conn: SnowflakeConnection = get_snowflake_connection()

        # Normalise DataFrame — work with a copy
        data: pd.DataFrame = df.copy()
        if "Date" not in data.columns:
            data = data.reset_index()
        data = data.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                     "Low": "low", "Close": "close", "Volume": "volume"})
        data["ticker"] = ticker
        data["date"] = pd.to_datetime(data["date"]).dt.date

        rows = data[["ticker", "date", "open", "high", "low", "close", "volume"]].values.tolist()

        with conn.cursor() as cur:
            # Delete stale rows
            cur.execute(
                f"DELETE FROM {SNOWFLAKE_TABLE_OHLCV} WHERE ticker = %s",
                (ticker,),
            )
            # Bulk insert
            cur.executemany(
                f"""
                INSERT INTO {SNOWFLAKE_TABLE_OHLCV}
                    (ticker, date, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )

        log_info(f"Saved {len(rows)} rows for {ticker} to Snowflake.")
    except Exception as exc:
        log_error(f"Failed to save OHLCV for {ticker} to Snowflake: {exc}")
        raise


# ── Read ──────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_ohlcv_from_snowflake(ticker: str, days: int = 365) -> pd.DataFrame:
    """Load OHLCV history for a ticker from Snowflake.

    Fetches rows from the last N calendar days ordered by date ascending.
    Result is cached for 1 hour with st.cache_data.

    Args:
        ticker: Ticker symbol (e.g. "AAPL").
        days:   Number of calendar days of history to return (default 365).

    Returns:
        DataFrame with columns: date, open, high, low, close, volume, ticker.
        Returns an empty DataFrame on failure.
    """
    sql: str = f"""
        SELECT ticker, date, open, high, low, close, volume
        FROM   {SNOWFLAKE_TABLE_OHLCV}
        WHERE  ticker = %s
          AND  date   >= DATEADD(day, -%s, CURRENT_DATE)
        ORDER BY date ASC
    """
    try:
        conn: SnowflakeConnection = get_snowflake_connection()
        with conn.cursor() as cur:
            cur.execute(sql, (ticker, days))
            rows = cur.fetchall()
            cols = [desc[0].lower() for desc in cur.description]

        df: pd.DataFrame = pd.DataFrame(rows, columns=cols)
        df["date"] = pd.to_datetime(df["date"])
        log_info(f"Loaded {len(df)} rows for {ticker} from Snowflake.")
        return df
    except Exception as exc:
        log_error(f"Failed to load OHLCV for {ticker} from Snowflake: {exc}")
        return pd.DataFrame()
