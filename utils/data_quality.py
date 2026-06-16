"""
utils/data_quality.py
=====================
OHLCV DataFrame validation utilities for NasdaqPulse.

All functions are pure Pandas — no external QA libraries required.
Run generate_quality_report() before loading data to MongoDB or Snowflake.
"""

import pandas as pd
import numpy as np
from typing import Dict

from utils.logger import log_info, log_error, log_warning

# Columns that must exist in a valid OHLCV DataFrame
_REQUIRED_COLUMNS: list[str] = ["Open", "High", "Low", "Close", "Volume"]


def validate_ohlcv_schema(df: pd.DataFrame) -> bool:
    """Check that the DataFrame contains all required OHLCV columns.

    The Date may be the index or a standalone column; either is accepted.

    Args:
        df: Raw OHLCV DataFrame returned by yfinance.

    Returns:
        True if all required columns are present, False otherwise.
    """
    existing = set(df.columns) | {df.index.name or ""}
    missing = [col for col in _REQUIRED_COLUMNS if col not in existing]
    if missing:
        log_error(f"Schema validation failed — missing columns: {missing}")
        return False
    log_info("Schema validation passed.")
    return True


def check_missing_values(df: pd.DataFrame) -> Dict[str, int]:
    """Count null values per column.

    Args:
        df: OHLCV DataFrame (index = Date).

    Returns:
        Dictionary mapping column name → number of null values,
        only for columns that contain at least one null.
    """
    null_counts: Dict[str, int] = (
        df.isnull().sum()
        .loc[lambda s: s > 0]
        .to_dict()
    )
    if null_counts:
        log_warning(f"Missing values detected: {null_counts}")
    else:
        log_info("No missing values found.")
    return null_counts


def check_duplicate_rows(df: pd.DataFrame) -> int:
    """Count rows with duplicate Date index values.

    Args:
        df: OHLCV DataFrame whose index represents trading dates.

    Returns:
        Number of duplicated date entries (0 = clean).
    """
    dupe_count: int = int(df.index.duplicated().sum())
    if dupe_count:
        log_warning(f"Found {dupe_count} duplicate date row(s).")
    else:
        log_info("No duplicate rows found.")
    return dupe_count


def detect_outliers_volume(df: pd.DataFrame) -> pd.DataFrame:
    """Flag rows where Volume exceeds mean + 3 * std as statistical outliers.

    Args:
        df: OHLCV DataFrame containing a 'Volume' column.

    Returns:
        Copy of df with a new boolean column 'is_outlier'.
        True means the row's volume is unusually high.
    """
    result: pd.DataFrame = df.copy()
    vol = result["Volume"]
    threshold: float = vol.mean() + 3 * vol.std()
    result["is_outlier"] = vol > threshold
    outlier_count: int = int(result["is_outlier"].sum())
    if outlier_count:
        log_warning(f"Detected {outlier_count} volume outlier row(s) (threshold={threshold:,.0f}).")
    else:
        log_info("No volume outliers detected.")
    return result


def generate_quality_report(df: pd.DataFrame, ticker: str) -> Dict:
    """Run all data-quality checks and produce a summary report.

    Quality score starts at 100 and is penalised as follows:
      - Schema invalid          : -40 points
      - Each column with nulls  : -10 points (capped at -30)
      - Any duplicate rows      : -15 points
      - Any volume outliers     : -5  points

    Args:
        df:     OHLCV DataFrame (index = Date).
        ticker: Ticker symbol string (e.g. "NVDA").

    Returns:
        Dictionary with keys:
            ticker        (str)
            total_rows    (int)
            missing_values (dict)
            duplicate_rows (int)
            outlier_rows   (int)
            schema_valid   (bool)
            quality_score  (float, 0–100)
    """
    log_info(f"Running data quality report for {ticker} ({len(df)} rows).")

    schema_valid: bool = validate_ohlcv_schema(df)
    missing: Dict[str, int] = check_missing_values(df)
    dupes: int = check_duplicate_rows(df)
    df_flagged: pd.DataFrame = detect_outliers_volume(df)
    outliers: int = int(df_flagged["is_outlier"].sum())

    # ── Quality score calculation ─────────────────────────────────────────
    score: float = 100.0
    if not schema_valid:
        score -= 40.0
    score -= min(len(missing) * 10.0, 30.0)   # cap missing penalty at 30
    if dupes > 0:
        score -= 15.0
    if outliers > 0:
        score -= 5.0
    score = max(score, 0.0)

    report: Dict = {
        "ticker": ticker,
        "total_rows": len(df),
        "missing_values": missing,
        "duplicate_rows": dupes,
        "outlier_rows": outliers,
        "schema_valid": schema_valid,
        "quality_score": round(score, 2),
    }

    log_info(
        f"Quality report for {ticker}: score={score:.1f}/100, "
        f"rows={len(df)}, dupes={dupes}, outliers={outliers}, "
        f"missing={missing}"
    )
    return report
