"""
pages/3_AI_Analyst.py
=====================
NasdaqPulse — Page 3: AI Analyst (RAG Mode)

RAG Pipeline:
  Step 1 — MongoDB $text search → relevant ticker documents
  Step 2 — Enrich with live yfinance snapshot (price + change %)
  Step 3 — Build context string → Groq llama-3.1-8b-instant → Thai answer
  Step 4 — Display response + context expander + chat history

Features:
  - Example question chips (click → auto-fill input)
  - Chat history (last 3 Q&A) stored in session_state
  - Clear history button
  - Full error handling with Thai-language user messages
"""

from __future__ import annotations

import time
from typing import Dict, List

import pandas as pd
import streamlit as st
from groq import Groq

from config.settings import (
    NASDAQ_100_TICKERS,
    TICKER_INFO,
    GROQ_API_KEY,
    GROQ_MODEL,
)
from utils.mongodb_client import search_ticker_context
from utils.yfinance_loader import get_latest_snapshot
from utils.logger import log_info, log_error, log_warning

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Analyst · NasdaqPulse",
    page_icon="🤖",
    layout="wide",
)

# ── Session state defaults ────────────────────────────────────────────────────
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = []          # list[dict{question, answer, context}]
if "ai_question_input" not in st.session_state:
    st.session_state["ai_question_input"] = ""


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.title("🤖 AI Analyst — RAG Mode")
st.caption(
    "MongoDB Text Search + Groq llama-3.1-8b-instant · Data: Nasdaq 100  |  "
    "ตอบเป็นภาษาไทย · ไม่เกิน 200 คำ"
)
st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# CHAT HISTORY  (shown above input)
# ══════════════════════════════════════════════════════════════════════════════

history: List[Dict] = st.session_state["chat_history"]

if history:
    st.subheader("💬 ประวัติการสนทนา (3 รายการล่าสุด)")
    recent = history[-3:]   # show last 3 only
    for i, entry in enumerate(reversed(recent), start=1):
        with st.expander(f"Q{i}: {entry['question'][:80]}{'…' if len(entry['question']) > 80 else ''}", expanded=(i == 1)):
            st.markdown(f"**❓ คำถาม:** {entry['question']}")
            st.markdown(f"**🤖 คำตอบ:**\n\n{entry['answer']}")
            if entry.get("context"):
                with st.expander("📄 Context ที่ใช้", expanded=False):
                    st.text(entry["context"])

    if st.button("🗑️ ล้างประวัติ", key="clear_history"):
        st.session_state["chat_history"] = []
        st.session_state["ai_question_input"] = ""
        st.rerun()

    st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE CHIPS
# ══════════════════════════════════════════════════════════════════════════════

EXAMPLE_QUESTIONS: List[str] = [
    "หุ้นกลุ่ม Technology ตัวไหนน่าสนใจ?",
    "NVDA กับ AVGO ต่างกันยังไง?",
    "หุ้นตัวไหนมี volume สูงสุดวันนี้?",
    "TSLA แนวโน้มเป็นยังไง?",
]

st.markdown("**💡 ตัวอย่างคำถาม:**")
chip_cols = st.columns(len(EXAMPLE_QUESTIONS))
for col, question in zip(chip_cols, EXAMPLE_QUESTIONS):
    with col:
        if st.button(question, key=f"chip_{question[:20]}", use_container_width=True):
            st.session_state["ai_question_input"] = question
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# INPUT BOX
# ══════════════════════════════════════════════════════════════════════════════

user_query: str = st.text_area(
    label="✏️ พิมพ์คำถามของคุณ",
    value=st.session_state["ai_question_input"],
    placeholder="ถามเกี่ยวกับหุ้น Nasdaq 100 เช่น 'NVDA มีแนวโน้มอย่างไร?' หรือ 'หุ้นกลุ่มไหนน่าสนใจ?'",
    height=100,
    key="query_textarea",
)

analyze_clicked = st.button("🔍 วิเคราะห์", type="primary", use_container_width=False)


# ══════════════════════════════════════════════════════════════════════════════
# RAG PIPELINE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _step1_retrieve(query: str) -> List[str]:
    """Step 1 — Retrieve relevant tickers from MongoDB $text search.

    Falls back to all NASDAQ_100_TICKERS if MongoDB is unavailable
    or returns no results.

    Args:
        query: User's natural-language question.

    Returns:
        List of ticker symbols relevant to the query.
    """
    try:
        results: List[Dict] = search_ticker_context(query)
        if results:
            tickers_found = [r["ticker"] for r in results if "ticker" in r]
            log_info(f"MongoDB text search found: {tickers_found}")
            return tickers_found
        else:
            log_warning("MongoDB text search returned 0 results — using all tickers as fallback.")
            return NASDAQ_100_TICKERS[:]
    except Exception as exc:
        log_warning(f"MongoDB search failed: {exc} — falling back to all tickers.")
        return NASDAQ_100_TICKERS[:]


def _compute_1m_return(ticker: str) -> float | None:
    """Compute 1-month return for a ticker using cached yfinance data."""
    try:
        from utils.yfinance_loader import fetch_ohlcv
        df = fetch_ohlcv(ticker, period="1mo")
        if df.empty or len(df) < 2:
            return None
        first_close = float(df["Close"].iloc[0])
        last_close  = float(df["Close"].iloc[-1])
        if first_close == 0:
            return None
        return round((last_close / first_close - 1) * 100, 2)
    except Exception:
        return None


def _step2_enrich(tickers: List[str]) -> tuple[pd.DataFrame, str]:
    """Step 2 — Fetch live snapshot and build context string.

    Args:
        tickers: List of ticker symbols to enrich.

    Returns:
        Tuple of (snapshot_df, context_string).
    """
    snapshot_df: pd.DataFrame = get_latest_snapshot(tickers)

    lines: List[str] = [
        "ข้อมูลหุ้น Nasdaq 100 (ราคาล่าสุด, % เปลี่ยนแปลง 1 วัน, % เปลี่ยนแปลง 1 เดือน):"
    ]

    if snapshot_df.empty:
        log_warning("Snapshot empty — building context from TICKER_INFO only.")
        for ticker in tickers:
            info = TICKER_INFO.get(ticker, {"name": ticker, "sector": "N/A"})
            lines.append(f"- {ticker} ({info['name']}, {info['sector']}): ไม่มีข้อมูลราคาล่าสุด")
    else:
        for _, row in snapshot_df.iterrows():
            ticker   = row["ticker"]
            info     = TICKER_INFO.get(ticker, {"name": ticker, "sector": "N/A"})
            close    = row.get("close", 0.0)
            chg_1d   = row.get("change_pct", 0.0)
            volume   = row.get("volume", 0)
            chg_1m   = _compute_1m_return(ticker)
            m_str    = f"{chg_1m:+.2f}%" if chg_1m is not None else "N/A"
            lines.append(
                f"- {ticker} ({info['name']}, {info['sector']}): "
                f"ราคา ${close:.2f}, เปลี่ยน 1 วัน {chg_1d:+.2f}%, "
                f"เปลี่ยน 1 เดือน {m_str}, Volume {volume:,}"
            )

    context_str: str = "\n".join(lines)
    return snapshot_df, context_str


def _step3_call_groq(context_str: str, query: str) -> str:
    """Step 3 — Send context + question to Groq and return Thai answer.

    Args:
        context_str: Enriched stock data context string.
        query:       User's original question.

    Returns:
        Thai narrative answer from llama-3.1-8b-instant.

    Raises:
        Exception: Propagates Groq API errors to the caller.
    """
    system_prompt = (
        "คุณคือนักวิเคราะห์การลงทุนที่เชี่ยวชาญหุ้น Nasdaq 100 "
        "ตอบคำถามโดยอิงจาก Context ที่ให้มาเท่านั้น อย่าสร้างข้อมูลขึ้นเอง "
        "Context มีข้อมูล: ราคาล่าสุด, % เปลี่ยนแปลง 1 วัน, % เปลี่ยนแปลง 1 เดือน, Volume "
        "ตอบเป็นภาษาไทย กระชับ ชัดเจน 3-5 ประโยค "
        "ถ้าถามเปรียบเทียบให้ใช้ตัวเลขจาก Context เสมอ "
        "ไม่แนะนำให้ซื้อหรือขายหุ้น ไม่ต้องบอกว่าเป็น AI"
    )
    user_prompt = f"Context:\n{context_str}\n\nคำถาม: {query}"

    log_info(f"Groq RAG request — query: '{query[:60]}…' | context: {len(context_str)} chars")
    t0 = time.time()

    client = Groq(api_key=GROQ_API_KEY)
    _models = [GROQ_MODEL, "llama3-8b-8192", "llama-3.1-8b-instant", "gemma2-9b-it"]
    response = None
    last_exc = None
    for _model in _models:
        try:
            response = client.chat.completions.create(
                model=_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.5,
                max_tokens=512,
            )
            log_info(f"Groq RAG used model: {_model}")
            break
        except Exception as _e:
            log_warning(f"Groq model {_model} failed: {_e}")
            last_exc = _e
    if response is None:
        raise last_exc or RuntimeError("All Groq models failed.")

    elapsed = round(time.time() - t0, 2)
    content: str = response.choices[0].message.content or ""
    log_info(f"Groq RAG response received in {elapsed}s ({len(content)} chars).")
    return content


# ══════════════════════════════════════════════════════════════════════════════
# MAIN: RUN PIPELINE ON BUTTON CLICK
# ══════════════════════════════════════════════════════════════════════════════

if analyze_clicked:
    query_clean = user_query.strip()
    if not query_clean:
        st.warning("⚠️ กรุณาพิมพ์คำถามก่อนกด วิเคราะห์")
        st.stop()

    # ── Pipeline execution ────────────────────────────────────────────────────
    mongo_fallback_used: bool = False
    context_str: str = ""
    ai_answer: str = ""

    with st.spinner("🔍 กำลังค้นหาข้อมูลจาก MongoDB..."):
        tickers_found = _step1_retrieve(query_clean)
        # Detect if fallback was triggered (all 10 tickers returned)
        if set(tickers_found) == set(NASDAQ_100_TICKERS):
            mongo_fallback_used = True

    if mongo_fallback_used:
        st.warning("⚠️ ใช้ข้อมูลสำรองแทน — ค้นหา MongoDB ไม่พบผลลัพธ์ที่ตรงกัน")

    with st.spinner("📡 กำลังดึงข้อมูลราคาล่าสุด..."):
        _, context_str = _step2_enrich(tickers_found)

    with st.spinner("🤖 AI กำลังวิเคราะห์..."):
        try:
            ai_answer = _step3_call_groq(context_str, query_clean)
        except Exception as exc:
            log_error(f"Groq RAG call failed: {exc}")
            st.error("❌ ไม่สามารถเชื่อมต่อ AI ได้ กรุณาลองใหม่")
            st.stop()

    # ── Save to chat history ──────────────────────────────────────────────────
    st.session_state["chat_history"].append({
        "question": query_clean,
        "answer":   ai_answer,
        "context":  context_str,
    })
    # Clear input so the box resets cleanly
    st.session_state["ai_question_input"] = ""

    # ── Step 4: Display results ───────────────────────────────────────────────
    st.divider()
    st.subheader("💡 ผลการวิเคราะห์")

    # RAG pipeline badge
    col_b1, col_b2, col_b3 = st.columns(3)
    with col_b1:
        st.success(f"✅ MongoDB ค้นพบ {len(tickers_found)} ticker(s)")
    with col_b2:
        st.info(f"📊 Context: {len(context_str)} chars")
    with col_b3:
        st.info(f"🤖 Model: {GROQ_MODEL}")

    st.markdown("---")
    st.markdown(ai_answer)
    st.markdown("---")

    with st.expander("📄 Context ที่ใช้ในการวิเคราะห์"):
        st.text(context_str)

    st.caption("⚠️ AI นี้ให้ข้อมูลเพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน")


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR INFO
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## 🤖 AI Analyst")
    st.divider()
    st.markdown("**RAG Pipeline:**")
    st.markdown(
        "1. 🔍 MongoDB Text Search\n"
        "2. 📡 yfinance Live Snapshot\n"
        "3. 🤖 Groq llama-3.1-8b-instant\n"
        "4. 🇹🇭 Thai Response"
    )
    st.divider()
    st.markdown(f"**Model:** `{GROQ_MODEL}`")
    st.markdown(f"**Tickers:** {len(NASDAQ_100_TICKERS)} ตัว")
    st.markdown(f"**History:** {len(st.session_state['chat_history'])} รายการ")
    st.divider()
    st.caption("⚠️ เพื่อการศึกษาเท่านั้น\nไม่ใช่คำแนะนำการลงทุน")
