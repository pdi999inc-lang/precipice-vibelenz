"""
Copyright © 2026 Ricky Sessums. All rights reserved.

Tests for app/literacy.py. In-memory sqlite shim in place of Postgres
(same pattern as tests/test_email_reminders.py).
Run: python -m pytest tests/test_literacy.py -v
"""
import json
import logging
import sqlite3
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import literacy as lit

RID = "3f2c9a10-1111-4a2b-9c3d-abcdefabcdef"


def _payload(**kw):
    base = {
        "request_id": RID,
        "lane": "RELATIONSHIP_NORMAL",
        "presentation_mode": "connection",
        "degraded": False,
        "risk_level": "LOW",
        "positive_signals": ["warm_reception_present", "playful_engagement_present"],
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- sqlite shim
class _Cur:
    def __init__(self, cur):
        self._c = cur

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?").replace("TIMESTAMPTZ", "TEXT")
        params = tuple(p.isoformat() if isinstance(p, datetime) else p for p in params)
        self._c.execute(sql, params)

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()


class _Conn:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _Cur(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def close(self):
        pass


@pytest.fixture(autouse=True)
def db(monkeypatch):
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    raw.execute(
        "CREATE TABLE analyses (request_id TEXT PRIMARY KEY, lane TEXT, presentation_mode TEXT, "
        "degraded BOOLEAN, positive_signals TEXT, risk_level TEXT, conversation_text TEXT)"
    )
    shim = _Conn(raw)
    monkeypatch.setattr(lit, "_connect", lambda: shim)
    monkeypatch.setattr(lit, "_schema_ready", False)
    return raw


def _seed(db, **kw):
    p = _payload(**kw)
    db.execute(
        "INSERT INTO analyses VALUES (?,?,?,?,?,?,?)",
        (p["request_id"], p["lane"], p["presentation_mode"], p["degraded"],
         json.dumps(p["positive_signals"]), p["risk_level"], "SECRET CONVERSATION TEXT"),
    )
    db.commit()
    return lit.build_prompt(p)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(lit.router)
    return TestClient(app)


# ---------------------------------------------------------------- build_prompt
def test_prompt_is_deterministic():
    assert lit.build_prompt(_payload()) == lit.build_prompt(_payload())


def test_prompt_shape():
    p = lit.build_prompt(_payload())
    keys = [o["key"] for o in p["options"]]
    assert p["version"] == lit.SCHEMA_VERSION
    assert len(keys) == lit.TOTAL_OPTIONS
    assert len(set(keys)) == len(keys)
    assert set(p["detected"]) <= set(keys)
    assert set(p["detected"]) == {"warm_reception_present", "playful_engagement_present"}


def test_order_varies_by_request_id():
    a = [o["key"] for o in lit.build_prompt(_payload())["options"]]
    b = [o["key"] for o in lit.build_prompt(_payload(request_id="9f2c9a10-2222-4a2b-9c3d-abcdefabcdef"))["options"]]
    assert sorted(a) != sorted(b) or a != b


def test_detected_capped_and_min_distractors():
    many = ["reciprocal_engagement", "warm_reception_present", "playful_engagement_present",
            "repair_attempt_present", "mutual_curiosity"]
    p = lit.build_prompt(_payload(positive_signals=many))
    assert len(p["detected"]) == lit.MAX_DETECTED_OPTIONS
    assert len(p["options"]) - len(p["detected"]) >= lit.MIN_DISTRACTORS


@pytest.mark.parametrize("override", [
    {"lane": "FRAUD"},
    {"lane": "coercion_risk"},
    {"presentation_mode": "risk"},
    {"degraded": True},
    {"risk_level": "WITHHELD"},
    {"positive_signals": []},
    {"positive_signals": ["No signals detected", "BAD KEY"]},
    {"positive_signals": None},
    {"request_id": "x"},
])
def test_ineligible_reads_get_no_card(override):
    assert lit.build_prompt(_payload(**override)) is None


def test_positive_signals_as_json_string_accepted():
    p = lit.build_prompt(_payload(positive_signals=json.dumps(["warm_reception_present"])))
    assert p["detected"] == ["warm_reception_present"]


def test_non_dict_returns_none():
    assert lit.build_prompt(None) is None
    assert lit.build_prompt("x") is None


def test_connection_voice_no_scanner_vocabulary():
    banned = ("signal", "flag", "risk", "pressure", "danger")
    labels = [lit.friendly_label(k) for k in list(lit.DISTRACTOR_BANK) + list(lit.FRIENDLY_LABELS)]
    text = " ".join(labels + [lit.QUESTION, lit.SUBTEXT]).lower()
    assert not any(b in text for b in banned)


# ---------------------------------------------------------------- endpoint
def test_stores_answer(client, db):
    p = _seed(db)
    sel = [p["detected"][0], [k["key"] for k in p["options"] if k["key"] not in p["detected"]][0]]
    r = client.post("/literacy/answer", json={"request_id": RID, "selected": sel})
    assert r.status_code == 200 and r.json() == {"stored": True}
    row = db.execute("SELECT * FROM literacy_labels").fetchone()
    cols = [d[0] for d in db.execute("SELECT * FROM literacy_labels").description]
    rec = dict(zip(cols, row))
    assert rec["hit_count"] == 1 and rec["missed_count"] == 1 and rec["extra_count"] == 1
    assert rec["schema_version"] == lit.SCHEMA_VERSION


def test_label_row_contains_no_conversation_text_or_identity(client, db):
    p = _seed(db)
    client.post("/literacy/answer", json={"request_id": RID, "selected": p["detected"][:1]})
    cols = [d[0] for d in db.execute("SELECT * FROM literacy_labels").description]
    assert not any(c in cols for c in ("visitor_id", "email", "conversation_text", "vl_vid"))
    dump = json.dumps(db.execute("SELECT * FROM literacy_labels").fetchall())
    assert "SECRET CONVERSATION TEXT" not in dump


def test_consent_field_is_ignored_notice_only_model(client, db):
    """Storage is covered by the front-door training notice, not a checkbox.
    A stale client sending consent=False must still store (and must not crash)."""
    p = _seed(db)
    r = client.post("/literacy/answer",
                    json={"request_id": RID, "selected": p["detected"][:1], "consent": False})
    assert r.json() == {"stored": True}
    assert db.execute("SELECT COUNT(*) FROM literacy_labels").fetchone()[0] == 1


def test_pick_not_on_card_refused(client, db):
    _seed(db)
    r = client.post("/literacy/answer", json={"request_id": RID, "selected": ["future_planning_x"], })
    assert r.status_code == 422 and r.json()["reason"] == "pick_not_on_card"


@pytest.mark.parametrize("lane,mode,degraded", [
    ("FRAUD", "connection", False),
    ("COERCION_RISK", "connection", False),
    ("BENIGN", "risk", False),
    ("BENIGN", "connection", True),
])
def test_server_rechecks_eligibility(client, db, lane, mode, degraded):
    _seed(db, lane=lane, presentation_mode=mode, degraded=degraded)
    r = client.post("/literacy/answer",
                    json={"request_id": RID, "selected": ["warm_reception_present"], })
    assert r.status_code == 403 and r.json()["stored"] is False


def test_unknown_request_refused(client, db):
    r = client.post("/literacy/answer",
                    json={"request_id": RID, "selected": ["warm_reception_present"], })
    assert r.status_code == 404


def test_duplicate_answer_not_overwritten(client, db):
    p = _seed(db)
    body = {"request_id": RID, "selected": p["detected"][:1], }
    assert client.post("/literacy/answer", json=body).json()["stored"] is True
    r = client.post("/literacy/answer", json={**body, "selected": p["detected"]})
    assert r.json() == {"stored": False, "reason": "already_answered"}
    assert db.execute("SELECT COUNT(*) FROM literacy_labels").fetchone()[0] == 1


@pytest.mark.parametrize("body", [
    {"request_id": "bad id!", "selected": ["a_b"], },
    {"request_id": RID, "selected": [], },
    {"request_id": RID, "selected": "warm_reception_present", },
    {"request_id": RID, "selected": ["x"] * 6, },
    {"request_id": RID, "selected": ["DROP TABLE"], },
    ["not", "a", "dict"],
])
def test_malformed_input_rejected(client, db, body):
    r = client.post("/literacy/answer", json=body)
    assert r.status_code in (400, 422)
    assert r.json()["stored"] is False


def test_db_failure_fails_closed(client, monkeypatch):
    def boom():
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(lit, "_connect", boom)
    r = client.post("/literacy/answer",
                    json={"request_id": RID, "selected": ["warm_reception_present"], })
    assert r.status_code == 503 and r.json()["stored"] is False


def test_every_decision_is_logged(client, db, caplog):
    p = _seed(db)
    with caplog.at_level(logging.INFO, logger="vibelenz.literacy"):
        client.post("/literacy/answer", json={"request_id": RID, "selected": p["detected"][:1], })
        client.post("/literacy/answer", json={"request_id": RID, "selected": ["not_on_card"]})
    decisions = [r.getMessage() for r in caplog.records if "literacy decision=" in r.getMessage()]
    assert any("decision=STORED" in m for m in decisions)
    assert any("decision=REFUSED" in m for m in decisions)
