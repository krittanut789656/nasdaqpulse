"""
utils/logger.py
===============
Simple logging utility for NasdaqPulse.

Logs are written to:
  - logs/app.log  (file, rotating-friendly)
  - stdout/stderr  (console)

Log format: [TIMESTAMP] [LEVEL] message

Usage:
    from utils.logger import log_info, log_error, log_warning

    log_info("Data fetched successfully")
    log_error("Snowflake connection failed")
    log_warning("Missing data for ASML")
"""

import logging
import os
from pathlib import Path

# ── Setup ─────────────────────────────────────────────────────────────────────
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "app.log"

_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Root logger for NasdaqPulse (avoid polluting the global root logger)
_logger = logging.getLogger("nasdaqpulse")
_logger.setLevel(logging.DEBUG)

if not _logger.handlers:
    # File handler
    _fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    _logger.addHandler(_fh)

    # Console handler
    _ch = logging.StreamHandler()
    _ch.setLevel(logging.INFO)
    _ch.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    _logger.addHandler(_ch)


# ── Public helpers ────────────────────────────────────────────────────────────

def log_info(msg: str) -> None:
    """Log an informational message.

    Args:
        msg: The message string to log at INFO level.
    """
    _logger.info(msg)


def log_error(msg: str) -> None:
    """Log an error message.

    Args:
        msg: The message string to log at ERROR level.
    """
    _logger.error(msg)


def log_warning(msg: str) -> None:
    """Log a warning message.

    Args:
        msg: The message string to log at WARNING level.
    """
    _logger.warning(msg)
