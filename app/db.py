from __future__ import annotations
import json
import logging
import os
import uuid

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
    """Idempotent. Safe to run on every startup. Adds missing columns without dropping data."""
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
        for column_sql in [
            "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS analysis_mode TEXT",
            "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS llm_enriched BOOLEAN DEFAULT NULL",
            "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS llm_error TEXT",
            "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS degraded BOOLEAN DEFAULT FALSE",
            "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS user_side TEXT",
            "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS other_gender TEXT",
            "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS utm_source TEXT",
            "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS utm_medium TEXT",
            "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS utm_campaign TEXT",
        ]:
            try:
                cur.execute(column_sql)
            except Exception as e:
                logger.warning(f"Column add skipped ({column_sql}): {e}")

        # --- Phase 1: multi-session conversation continuity (anonymous, device-local) ---
        # Additive only. No FK constraints to avoid insert-ordering failures under load.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                last_updated    TIMESTAMPTZ DEFAULT NOW(),
                relationship_type TEXT,
                user_email      TEXT DEFAULT NULL,
                expires_at      TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 days'),
                batch_count     INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversation_batches (
                id              SERIAL PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                request_id      TEXT,
                batch_number    INTEGER NOT NULL,
                ocr_text        TEXT,
                risk_score      INTEGER,
                risk_level      TEXT,
                primary_label   TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_batches_cid
            ON conversation_batches (conversation_id, batch_number)
        """)

        conn.commit()
        cur.close()
        logger.info("DB initialized")
    except Exception as e:
        logger.warning(f"DB init failed: {e}")
    finally:
        conn.close()


def log_analysis(payload: dict, conversation_text: str = "",
                 utm_source: str = "", utm_medium: str = "", utm_campaign: str = "",
                 analysis_mode: str = ""):
    conn = get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO analyses (
                request_id, relationship_type, requested_mode,
                risk_score, risk_level, primary_label, lane,
                presentation_mode, flags, positive_signals, conversation_text,
                analysis_mode, llm_enriched, llm_error, degraded,
                user_side, other_gender,
                utm_source, utm_medium, utm_campaign
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
            conversation_text[:8000] if conversation_text else "",
            payload.get("analysis_mode"),
            payload.get("llm_enriched"),
            payload.get("llm_error"),
            bool(payload.get("degraded", False)),
            payload.get("user_side"),
            payload.get("other_gender"),
            utm_source or None,
            utm_medium or None,
            utm_campaign or None,
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
        """, (accurate, note[:2000] if note else "", request_id))
        rows = cur.rowcount
        conn.commit()
        cur.close()
        if rows == 0:
            logger.warning(f"Feedback received for unknown request_id: {request_id}")
    except Exception as e:
        logger.warning(f"DB feedback failed: {e}")
    finally:
        conn.close()


# ===========================================================================
# Phase 1: Multi-session conversation continuity (anonymous, device-local)
# All functions fail closed: on any error, log and return a safe default so
# that a continuity failure degrades to single-batch analysis and never
# blocks a read.
# ===========================================================================

def get_or_create_conversation(conversation_id: str = "",
                               relationship_type: str = "stranger",
                               user_email: str = None) -> dict:
    """
    Return existing non-expired conversation, or create a new one.
    Returns dict: {conversation_id, batch_count, is_new}.
    On DB failure returns a safe ephemeral dict with a fresh id so the
    caller can still proceed without continuity.
    """
    new_id = conversation_id or str(uuid.uuid4())
    safe_default = {"conversation_id": new_id, "batch_count": 0, "is_new": True}
    conn = get_conn()
    if not conn:
        return safe_default
    try:
        cur = conn.cursor()
        row = None
        if conversation_id:
            cur.execute("""
                SELECT conversation_id, batch_count
                FROM conversations
                WHERE conversation_id = %s AND expires_at > NOW()
            """, (conversation_id,))
            row = cur.fetchone()

        if row:
            cur.execute("""
                UPDATE conversations SET last_updated = NOW()
                WHERE conversation_id = %s
            """, (conversation_id,))
            conn.commit()
            cur.close()
            return {"conversation_id": row[0], "batch_count": int(row[1] or 0), "is_new": False}

        # Create new (either none provided, or prior expired/not found)
        cur.execute("""
            INSERT INTO conversations (conversation_id, relationship_type, user_email)
            VALUES (%s, %s, %s)
            ON CONFLICT (conversation_id) DO UPDATE SET last_updated = NOW()
        """, (new_id, relationship_type, user_email))
        conn.commit()
        cur.close()
        return {"conversation_id": new_id, "batch_count": 0, "is_new": True}
    except Exception as e:
        logger.warning(f"get_or_create_conversation failed: {e}")
        return safe_default
    finally:
        conn.close()


def get_accumulated_context(conversation_id: str, char_cap: int = 6000) -> str:
    """
    Concatenate all prior batch OCR text for this conversation, oldest first.
    Truncate from the FRONT if over char_cap (keep most recent context).
    On failure returns "" so analysis proceeds on the new batch alone.
    """
    if not conversation_id:
        return ""
    conn = get_conn()
    if not conn:
        return ""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ocr_text FROM conversation_batches
            WHERE conversation_id = %s
            ORDER BY batch_number ASC
        """, (conversation_id,))
        rows = cur.fetchall()
        cur.close()
        joined = "\n\n".join((r[0] or "") for r in rows if r and r[0])
        if len(joined) > char_cap:
            joined = joined[-char_cap:]
        return joined
    except Exception as e:
        logger.warning(f"get_accumulated_context failed: {e}")
        return ""
    finally:
        conn.close()


def save_batch(conversation_id: str, request_id: str, ocr_text: str,
               risk_score: int, risk_level: str, primary_label: str) -> int:
    """
    Insert a new frozen batch row and increment the conversation batch_count.
    The stored score is immutable — never updated by future visits.
    Returns the assigned batch_number, or 0 on failure.
    """
    if not conversation_id:
        return 0
    conn = get_conn()
    if not conn:
        return 0
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(MAX(batch_number), 0) FROM conversation_batches
            WHERE conversation_id = %s
        """, (conversation_id,))
        next_num = int((cur.fetchone() or [0])[0] or 0) + 1
        cur.execute("""
            INSERT INTO conversation_batches
                (conversation_id, request_id, batch_number, ocr_text,
                 risk_score, risk_level, primary_label)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            conversation_id, request_id, next_num,
            (ocr_text or "")[:8000], risk_score, risk_level, primary_label,
        ))
        cur.execute("""
            UPDATE conversations
            SET batch_count = %s, last_updated = NOW()
            WHERE conversation_id = %s
        """, (next_num, conversation_id))
        conn.commit()
        cur.close()
        return next_num
    except Exception as e:
        logger.warning(f"save_batch failed: {e}")
        return 0
    finally:
        conn.close()


def get_conversation_batches(conversation_id: str) -> list:
    """
    Return all frozen batch scores oldest first, for trend display.
    Each item: {batch_number, risk_score, risk_level, primary_label, created_at}.
    On failure returns [].
    """
    if not conversation_id:
        return []
    conn = get_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT batch_number, risk_score, risk_level, primary_label, created_at
            FROM conversation_batches
            WHERE conversation_id = %s
            ORDER BY batch_number ASC
        """, (conversation_id,))
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "batch_number": int(r[0]),
                "risk_score": r[1],
                "risk_level": r[2],
                "primary_label": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"get_conversation_batches failed: {e}")
        return []
    finally:
        conn.close()
