"""
utils/yfinance_loader.py
========================
Yahoo Finance data-fetching layer for NasdaqPulse.
"""

from __future__ import annotations

import math
from typing import Dict, List

import pandas as pd
import yfinance as yf
import streamlit as st

from config.settings import DEFAULT_PERIOD, DEFAULT_INTERVAL
from utils.data_quality import generate_quality_report
from utils.logger import log_info, log_error, log_warning


def _scalar(val) -> float:
    """Safely convert yfinance cell (scalar or Series) to float."""
    if isinstance(val, pd.Series):
        return float(val.iloc[0]) if not val.empty else float("nan")
    return float(val)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ohlcv(ticker: str, period: str = DEFAULT_PERIOD) -> pd.DataFrame:
    log_info(f"Fetching OHLCV for {ticker} (period={period}) ...")
    try:
        raw: pd.DataFrame = yf.download(
            ticker,
            period=period,
            interval=DEFAULT_INTERVAL,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw.empty:
            log_warning(f"yfinance returned empty DataFrame for {ticker}.")
            return pd.DataFrame()
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        cols_wanted = ["Open", "High", "Low", "Close", "Volume"]
        df = raw[[c for c in cols_wanted if c in raw.columns]].copy()
        df.index.name = "Date"
        report = generate_quality_report(df, ticker)
        log_info(f"Quality score for {ticker}: {report['quality_score']}/100")
        return df
    except Exception as exc:
        log_error(f"Failed to fetch OHLCV for {ticker}: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_multiple_tickers(
    tickers: List[str],
    period: str = DEFAULT_PERIOD,
) -> Dict[str, pd.DataFrame]:
    results: Dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        df = fetch_ohlcv(ticker, period=period)
        if not df.empty:
            results[ticker] = df
        else:
            log_warning(f"Skipping {ticker} - no data returned.")
    log_info(f"Fetched {len(results)}/{len(tickers)} tickers successfully.")
    return results


@st.cache_data(ttl=900, show_spinner=False)
def get_latest_snapshot(tickers: List[str]) -> pd.DataFrame:
    log_info(f"Fetching latest snapshot for {len(tickers)} tickers ...")
    records = []
    for ticker in tickers:
        try:
            raw: pd.DataFrame = yf.download(
                ticker,
                period="5d",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            if raw.empty or len(raw) < 2:
                log_warning(f"Not enough data for snapshot of {ticker}.")
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw = raw.dropna(subset=["Close"])
            if len(raw) < 2:
                log_warning(f"Not enough non-NaN rows for {ticker}.")
                continue
            latest = raw.iloc[-1]
            prev = raw.iloc[-2]
            close = _scalar(latest["Close"])
            prev_close = _scalar(prev["Close"])
            volume = int(_scalar(latest["Volume"]))
            if math.isnan(close) or math.isnan(prev_close):
                log_warning(f"NaN price for {ticker}, skipping.")
                continue
            change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
            records.append({
                "ticker": ticker,
                "close": round(close, 4),
                "prev_close": round(prev_close, 4),
                "change_pct": round(change_pct, 4),
                "volume": volume,
            })
        except Exception as exc:
            log_error(f"Snapshot fetch failed for {ticker}: {exc}")
    df = pd.DataFrame(records)
    log_info(f"Snapshot ready: {len(df)} tickers.")
    return df
