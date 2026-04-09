from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("vibelenz.db")


def get_conn():
    try:
        import psycopg2
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return None
        return psycopg2.connect(url)
    except Exception as e:
        logger.warning(f"DB connection failed: {e}")
        return None


def init_db():
    conn = get_conn()
    if not conn:
        logger.warning("init_db: no DB connection, skipping")
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id SERIAL PRIMARY KEY,
                request_id TEXT UNIQUE NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                relationship_type TEXT,
                requested_mode TEXT,
                risk_score INTEGER,
                risk_level TEXT,
                primary_label TEXT,
                lane TEXT,
                presentation_mode TEXT,
                flags JSONB,
                positive_signals JSONB,
                conversation_text TEXT,
                feedback_accurate BOOLEAN DEFAULT NULL,
                feedback_note TEXT DEFAULT NULL,
                feedback_at TIMESTAMPTZ DEFAULT NULL
            )
        """)
        conn.commit()
        cur.close()
        logger.info("DB initialized")
    except Exception as e:
        logger.warning(f"DB init failed: {e}")
    finally:
        conn.close()


def log_analysis(payload: dict, conversation_text: str = ""):
    conn = get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO analyses (
                request_id, relationship_type, requested_mode,
                risk_score, risk_level, primary_label, lane,
                presentation_mode, flags, positive_signals, conversation_text
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (request_id) DO NOTHING
        """, (
            payload.get("request_id"),
            payload.get("relationship_type", "stranger"),
            payload.get("requested_mode", "risk"),
            payload.get("risk_score"),
            payload.get("risk_level"),
            payload.get("primary_label"),
            payload.get("lane"),
            payload.get("presentation_mode"),
            json.dumps(payload.get("flags", [])),
            json.dumps(payload.get("positive_signals", [])),
            conversation_text[:5000] if conversation_text else "",
        ))
        conn.commit()
        cur.close()
    except Exception as e:
        logger.warning(f"DB log failed: {e}")
    finally:
        conn.close()


def log_feedback(request_id: str, accurate: bool, note: str = ""):
    conn = get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE analyses
            SET feedback_accurate = %s,
                feedback_note = %s,
                feedback_at = NOW()
            WHERE request_id = %s
        """, (accurate, note[:500] if note else "", request_id))
        conn.commit()
        cur.close()
    except Exception as e:
        logger.warning(f"DB feedback failed: {e}")
    finally:
        conn.close()
