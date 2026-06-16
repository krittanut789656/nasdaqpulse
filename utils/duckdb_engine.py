"""
utils/duckdb_engine.py
======================
In-memory SQL analytics layer for NasdaqPulse using DuckDB.

DuckDB can query Pandas DataFrames directly via relation registration.
No data is persisted to disk — everything runs in-process.

RSI is computed with pure Pandas (no TA-Lib).
"""

from __future__ import annotations

from typing import Dict, Tuple

import duckdb
import pandas as pd

from utils.logger import log_info, log_error


# ── Core SQL helper ───────────────────────────────────────────────────────────

def query_df(
    df: pd.DataFrame,
    sql: str,
    table_name: str = "stock_data",
) -> pd.DataFrame:
    """Register a DataFrame as a DuckDB table and execute a SQL query.

    Args:
        df:         Input Pandas DataFrame to register.
        sql:        SQL query string that references ``table_name``.
        table_name: Name to register the DataFrame under (default "stock_data").

    Returns:
        Query result as a Pandas DataFrame.
        Returns an empty DataFrame on failure.

    Example:
        result = query_df(df, "SELECT ticker, close FROM stock_data WHERE change_pct > 2")
    """
    try:
        con = duckdb.connect(database=":memory:")
        con.register(table_name, df)
        result: pd.DataFrame = con.execute(sql).fetchdf()
        con.close()
        return result
    except Exception as exc:
        log_error(f"DuckDB query failed: {exc}\nSQL: {sql}")
        return pd.DataFrame()


# ── Top Movers ────────────────────────────────────────────────────────────────

def get_top_movers(
    snapshot_df: pd.DataFrame,
    n: int = 5,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return the top N gainers and top N losers from a snapshot DataFrame.

    Uses DuckDB SQL for the sort so it stays consistent with the rest
    of the analytics layer.

    Args:
        snapshot_df: DataFrame with columns ticker, close, prev_close,
                     change_pct, volume (output of get_latest_snapshot).
        n:           Number of tickers to return in each list (default 5).

    Returns:
        Tuple of (top_gainers, top_losers), each a DataFrame sorted by
        change_pct descending / ascending respectively.
    """
    if snapshot_df.empty:
        log_error("get_top_movers: snapshot_df is empty.")
        return pd.DataFrame(), pd.DataFrame()

    gainers: pd.DataFrame = query_df(
        snapshot_df,
        f"SELECT * FROM stock_data ORDER BY change_pct DESC LIMIT {n}",
    )
    losers: pd.DataFrame = query_df(
        snapshot_df,
        f"SELECT * FROM stock_data ORDER BY change_pct ASC LIMIT {n}",
    )
    log_info(f"Top movers computed: {len(gainers)} gainers, {len(losers)} losers.")
    return gainers, losers


# ── Sector Summary ────────────────────────────────────────────────────────────

def get_sector_summary(
    metadata_df: pd.DataFrame,
    snapshot_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute average daily change and ticker count per sector.

    Performs an in-memory JOIN between metadata (sector info) and the
    latest price snapshot, then aggregates by sector.

    Args:
        metadata_df: DataFrame with columns ticker, name, sector
                     (output of load_ticker_metadata).
        snapshot_df: DataFrame with columns ticker, close, change_pct, volume
                     (output of get_latest_snapshot).

    Returns:
        DataFrame with columns:
            sector       (str)   — sector name
            avg_change   (float) — average % change across tickers in sector
            ticker_count (int)   — number of tickers in sector
        Sorted by avg_change descending.
        Returns an empty DataFrame if either input is empty.
    """
    if metadata_df.empty or snapshot_df.empty:
        log_error("get_sector_summary: one or both input DataFrames are empty.")
        return pd.DataFrame()

    try:
        con = duckdb.connect(database=":memory:")
        con.register("meta", metadata_df)
        con.register("snap", snapshot_df)

        sql: str = """
            SELECT
                m.sector,
                ROUND(AVG(s.change_pct), 4)  AS avg_change,
                COUNT(m.ticker)               AS ticker_count
            FROM meta  m
            JOIN snap  s ON m.ticker = s.ticker
            GROUP BY m.sector
            ORDER BY avg_change DESC
        """
        result: pd.DataFrame = con.execute(sql).fetchdf()
        con.close()
        log_info(f"Sector summary computed for {len(result)} sector(s).")
        return result
    except Exception as exc:
        log_error(f"get_sector_summary failed: {exc}")
        return pd.DataFrame()


# ── KPI Stats ─────────────────────────────────────────────────────────────────

def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI using Wilder's smoothing (pure Pandas, no TA-Lib).

    Args:
        close:  Series of adjusted close prices (chronological order).
        period: Lookback window for RSI (default 14).

    Returns:
        Series of RSI values (0-100); NaN for the first ``period`` rows.
    """
    delta: pd.Series = close.diff()
    gain: pd.Series = delta.clip(lower=0)
    loss: pd.Series = (-delta).clip(lower=0)

    avg_gain: pd.Series = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss: pd.Series = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs: pd.Series = avg_gain / avg_loss.replace(0, float("nan"))
    rsi: pd.Series = 100 - (100 / (1 + rs))
    return rsi


def get_kpi_stats(ohlcv_df: pd.DataFrame) -> Dict[str, object]:
    """Compute key performance indicators from an OHLCV DataFrame.

    Calculates:
      - current_price   : latest adjusted close
      - return_30d      : % return over the last 30 trading days
      - avg_volume_30d  : average daily volume over the last 30 trading days
      - rsi_current     : most recent RSI-14 value (Pandas / Wilder method)

    Args:
        ohlcv_df: OHLCV DataFrame with DatetimeIndex and columns
                  Open, High, Low, Close, Volume.

    Returns:
        Dictionary with keys: current_price, return_30d, avg_volume_30d,
        rsi_current. Values are None if the DataFrame is too short.
    """
    result: Dict[str, object] = {
        "current_price": None,
        "return_30d": None,
        "avg_volume_30d": None,
        "rsi_current": None,
    }

    if ohlcv_df.empty or "Close" not in ohlcv_df.columns:
        log_error("get_kpi_stats: empty or invalid DataFrame supplied.")
        return result

    close: pd.Series = ohlcv_df["Close"].dropna()
    volume: pd.Series = ohlcv_df["Volume"].dropna()

    if len(close) < 2:
        return result

    # Current price
    result["current_price"] = round(float(close.iloc[-1]), 4)

    # 30-day return
    lookback: int = min(30, len(close) - 1)
    price_30d_ago: float = float(close.iloc[-lookback - 1])
    if price_30d_ago:
        result["return_30d"] = round(
            (float(close.iloc[-1]) / price_30d_ago - 1) * 100, 4
        )

    # 30-day average volume
    vol_30d: pd.Series = volume.iloc[-30:] if len(volume) >= 30 else volume
    result["avg_volume_30d"] = int(vol_30d.mean())

    # RSI-14
    if len(close) >= 15:
        rsi_series: pd.Series = _compute_rsi(close, period=14)
        rsi_val = rsi_series.iloc[-1]
        if pd.notna(rsi_val):
            result["rsi_current"] = round(float(rsi_val), 2)

    log_info(
        f"KPI stats: price={result['current_price']}, "
        f"return_30d={result['return_30d']}%, "
        f"avg_vol={result['avg_volume_30d']}, "
        f"RSI={result['rsi_current']}"
    )
    return result


# ── Market Intelligence Analytics ─────────────────────────────────────────────

def compute_market_indicators(ohlcv_dict: dict) -> pd.DataFrame:
    """Compute EMA20/50/200, RSI-14, and multi-period returns for all tickers.

    Args:
        ohlcv_dict: Mapping ticker → OHLCV DataFrame (DatetimeIndex, Close col).

    Returns:
        DataFrame with one row per ticker:
            ticker, ema20, ema50, ema200, rsi,
            close, return_3m, return_6m, return_12m, avg_volume
    """
    rows = []
    for ticker, df in ohlcv_dict.items():
        if df.empty or "Close" not in df.columns:
            continue
        close = df["Close"].dropna()
        vol   = df["Volume"].dropna() if "Volume" in df.columns else pd.Series(dtype=float)
        if len(close) < 30:
            continue
        # EMAs
        ema20  = float(close.ewm(span=20,  adjust=False).mean().iloc[-1])
        ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1]) if len(close) >= 50 else float("nan")
        # RSI
        rsi_series = _compute_rsi(close, 14)
        rsi = float(rsi_series.iloc[-1]) if pd.notna(rsi_series.iloc[-1]) else float("nan")
        # Current close
        current = float(close.iloc[-1])
        # Returns
        def _ret(n):
            if len(close) <= n:
                return float("nan")
            return round((current / float(close.iloc[-n-1]) - 1) * 100, 2)
        r3m  = _ret(63)
        r6m  = _ret(126)
        r12m = _ret(252)
        # Avg volume
        avg_vol = int(vol.iloc[-30:].mean()) if len(vol) >= 5 else 0
        rows.append({
            "ticker": ticker, "close": round(current, 2),
            "ema20": round(ema20, 2), "ema50": round(ema50, 2), "ema200": round(ema200, 2),
            "rsi": round(rsi, 2), "return_3m": r3m, "return_6m": r6m, "return_12m": r12m,
            "avg_volume": avg_vol,
        })
    result = pd.DataFrame(rows)
    log_info(f"compute_market_indicators: {len(result)} tickers processed.")
    return result


def compute_correlation_matrix(ohlcv_dict: dict) -> pd.DataFrame:
    """Compute Pearson correlation matrix of daily returns via DuckDB.

    Args:
        ohlcv_dict: Mapping ticker → OHLCV DataFrame.

    Returns:
        Square DataFrame of Pearson correlations (tickers × tickers).
    """
    series = {}
    for ticker, df in ohlcv_dict.items():
        if df.empty or "Close" not in df.columns:
            continue
        ret = df["Close"].dropna().pct_change().dropna()
        if len(ret) > 30:
            series[ticker] = ret
    if not series:
        return pd.DataFrame()
    returns_df = pd.DataFrame(series).dropna()
    corr = returns_df.corr(method="pearson")
    log_info(f"Correlation matrix computed: {corr.shape[0]}×{corr.shape[1]}")
    return corr


def compute_risk_return(ohlcv_dict: dict, rf_rate: float = 0.05) -> pd.DataFrame:
    """Compute annualised return, volatility, and Sharpe ratio per ticker.

    Args:
        ohlcv_dict: Mapping ticker → OHLCV DataFrame.
        rf_rate:    Annual risk-free rate (default 5%).

    Returns:
        DataFrame with columns: ticker, ann_return, ann_vol, sharpe, avg_volume.
    """
    import math
    rows = []
    for ticker, df in ohlcv_dict.items():
        if df.empty or "Close" not in df.columns:
            continue
        close = df["Close"].dropna()
        vol   = df["Volume"].dropna() if "Volume" in df.columns else pd.Series(dtype=float)
        if len(close) < 30:
            continue
        daily_ret = close.pct_change().dropna()
        ann_return = round(float(daily_ret.mean()) * 252 * 100, 2)
        ann_vol    = round(float(daily_ret.std()) * math.sqrt(252) * 100, 2)
        sharpe     = round((ann_return/100 - rf_rate) / (ann_vol/100), 3) if ann_vol else float("nan")
        avg_vol    = int(vol.iloc[-30:].mean()) if len(vol) >= 5 else 0
        rows.append({
            "ticker": ticker, "ann_return": ann_return,
            "ann_vol": ann_vol, "sharpe": sharpe, "avg_volume": avg_vol,
        })
    result = pd.DataFrame(rows)
    log_info(f"compute_risk_return: {len(result)} tickers.")
    return result
