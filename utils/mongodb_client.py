"""
utils/mongodb_client.py
=======================
MongoDB helpers for NasdaqPulse.

Collections used:
  stock_db.tickers   — ticker metadata (upserted on each refresh)

All read-heavy operations are cached with st.cache_resource /
st.cache_data to minimise Atlas round-trips.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

import pandas as pd
import streamlit as st
from pymongo import MongoClient, TEXT
from pymongo.collection import Collection
from pymongo.database import Database

from config.settings import MONGODB_URI, MONGODB_DATABASE, TICKER_INFO
from utils.logger import log_info, log_error, log_warning


# ── Connection ────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def get_mongo_client() -> MongoClient:
    """Create and cache a MongoDB client using the URI from settings.

    Uses st.cache_resource so the connection is shared across reruns
    without being rebuilt on every Streamlit interaction.

    Returns:
        A connected PyMongo MongoClient instance.

    Raises:
        ConnectionError: If the MongoDB URI is empty or the ping fails.
    """
    log_info(f"Connecting to MongoDB (database={MONGODB_DATABASE}) …")
    try:
        client: MongoClient = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10_000)
        # Validate connection
        client.admin.command("ping")
        log_info("MongoDB connection established.")
        return client
    except Exception as exc:
        log_error(f"MongoDB connection failed: {exc}")
        raise ConnectionError(f"MongoDB connection failed: {exc}") from exc


def _get_tickers_collection() -> Collection:
    """Return the 'tickers' collection from stock_db.

    Returns:
        PyMongo Collection object for the tickers collection.
    """
    client: MongoClient = get_mongo_client()
    db: Database = client[MONGODB_DATABASE]
    return db["tickers"]


# ── Text Index ────────────────────────────────────────────────────────────────

def create_text_index() -> None:
    """Create a MongoDB text index on ticker, name, and sector fields.

    This enables $text search used by search_ticker_context().
    Safe to call multiple times — MongoDB ignores duplicate index creation.
    """
    col: Collection = _get_tickers_collection()
    try:
        col.create_index(
            [("ticker", TEXT), ("name", TEXT), ("sector", TEXT)],
            name="ticker_text_idx",
            default_language="english",
        )
        log_info("MongoDB text index ensured on tickers collection.")
    except Exception as exc:
        log_warning(f"Text index creation skipped or failed: {exc}")


# ── Write ─────────────────────────────────────────────────────────────────────

def save_ticker_metadata(tickers: List[str], latest_closes: Dict[str, float] | None = None) -> None:
    """Upsert metadata for each ticker into the 'tickers' collection.

    Each document contains: ticker, name, sector, last_updated, latest_close.
    Uses replace_one with upsert=True so re-runs are idempotent.

    Args:
        tickers:       List of ticker symbols (e.g. ["AAPL", "NVDA"]).
        latest_closes: Optional mapping of ticker → most recent close price.
    """
    col: Collection = _get_tickers_collection()
    latest_closes = latest_closes or {}
    saved: int = 0

    for ticker in tickers:
        info: Dict[str, str] = TICKER_INFO.get(ticker, {"name": ticker, "sector": "Unknown"})
        doc: Dict[str, Any] = {
            "ticker": ticker,
            "name": info["name"],
            "sector": info["sector"],
            "latest_close": latest_closes.get(ticker),
            "last_updated": datetime.datetime.utcnow().isoformat(),
        }
        try:
            col.replace_one({"ticker": ticker}, doc, upsert=True)
            saved += 1
        except Exception as exc:
            log_error(f"Failed to upsert metadata for {ticker}: {exc}")

    log_info(f"Upserted metadata for {saved}/{len(tickers)} tickers.")


# ── Read ──────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_ticker_metadata() -> pd.DataFrame:
    """Load all ticker documents from MongoDB into a Pandas DataFrame.

    Result is cached for 5 minutes (300 s) with st.cache_data.

    Returns:
        DataFrame with columns: ticker, name, sector, latest_close, last_updated.
        Returns an empty DataFrame if the collection is empty or unreachable.
    """
    try:
        col: Collection = _get_tickers_collection()
        docs: List[Dict] = list(col.find({}, {"_id": 0}))
        if not docs:
            log_warning("No ticker metadata found in MongoDB.")
            return pd.DataFrame()
        df: pd.DataFrame = pd.DataFrame(docs)
        log_info(f"Loaded {len(df)} ticker metadata records from MongoDB.")
        return df
    except Exception as exc:
        log_error(f"Failed to load ticker metadata: {exc}")
        return pd.DataFrame()


def search_ticker_context(query: str) -> List[Dict[str, Any]]:
    """Search ticker documents using MongoDB $text search.

    Searches across the 'ticker', 'name', and 'sector' text-indexed fields.
    Requires create_text_index() to have been called at least once.

    Args:
        query: Free-text search string (e.g. "semiconductor technology").

    Returns:
        List of up to 5 matching ticker documents (without MongoDB _id).
        Returns an empty list if the search fails or nothing matches.
    """
    try:
        col: Collection = _get_tickers_collection()
        cursor = col.find(
            {"$text": {"$search": query}},
            {"_id": 0, "score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(5)

        results: List[Dict[str, Any]] = list(cursor)
        log_info(f"MongoDB text search for '{query}' returned {len(results)} result(s).")
        return results
    except Exception as exc:
        log_error(f"MongoDB text search failed for query '{query}': {exc}")
        return []
