"""Copyright © 2026 Ricky Sessums. All rights reserved.

WP2b: one log row per AI call, keyed by request_id, with NO conversation content. Logging is
best-effort and never breaks an analysis. Outside a request (evals, health probe, scripts) no
context is set and nothing is logged.
"""
import asyncio
import contextvars
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.db as db_mod
import app.llm_util as llm_util

ROOT = Path(__file__).resolve().parent.parent


def _isolated(fn):
    """Run fn in a copy of the current context so a set_llm_context() never leaks between tests."""
    return contextvars.copy_context().run(fn)


class _Messages:
    def __init__(self, result=None, error=None):
        self._result, self._error, self.seen = result, error, []

    def create(self, **kwargs):
        self.seen.append(kwargs)
        if self._error:
            raise self._error
        return self._result


def _client(result=None, error=None):
    return SimpleNamespace(messages=_Messages(result, error))


def _msg(inp=11, out=7):
    return SimpleNamespace(content=[], usage=SimpleNamespace(input_tokens=inp, output_tokens=out))


@pytest.fixture
def rows(monkeypatch):
    got = []
    monkeypatch.setattr(db_mod, "log_llm_call", lambda **kw: got.append(kw))
    return got


def test_logged_create_passes_through_and_logs_tokens(rows):
    client, msg = _client(_msg(11, 7)), None

    def run():
        llm_util.set_llm_context("rid-a", path="paste", n_images=0)
        return llm_util.logged_create(client, "analysis", model="m-1", max_tokens=5)

    msg = _isolated(run)
    assert msg is client.messages._result
    assert client.messages.seen == [{"model": "m-1", "max_tokens": 5}]
    assert len(rows) == 1
    r = rows[0]
    assert (r["request_id"], r["call_site"], r["model"], r["path"], r["n_images"]) == ("rid-a", "analysis", "m-1", "paste", 0)
    assert (r["input_tokens"], r["output_tokens"], r["status"]) == (11, 7, "ok")
    assert isinstance(r["latency_ms"], int) and r["latency_ms"] >= 0


def test_rows_carry_no_conversation_content(rows):
    client = _client(_msg())

    def run():
        llm_util.set_llm_context("rid-b", path="screenshot", n_images=2)
        llm_util.logged_create(client, "narrative", model="m", system="SECRET PROMPT",
                               messages=[{"role": "user", "content": "SECRET CHAT TEXT"}])

    _isolated(run)
    allowed = {"request_id", "call_site", "model", "input_tokens", "output_tokens", "latency_ms",
               "status", "lane", "path", "n_images"}
    assert set(rows[0]) <= allowed
    assert "SECRET" not in repr(rows)


def test_no_context_means_no_rows(rows):
    client = _client(_msg())
    _isolated(lambda: llm_util.logged_create(client, "analysis", model="m"))
    assert rows == []  # evals / health probe / scripts run outside a request


def test_error_still_logs_a_row_and_reraises(rows):
    client = _client(error=RuntimeError("upstream down"))

    def run():
        llm_util.set_llm_context("rid-c")
        with pytest.raises(RuntimeError):
            llm_util.logged_create(client, "reply", model="m")

    _isolated(run)
    assert len(rows) == 1 and rows[0]["status"] == "error" and rows[0]["input_tokens"] is None


def test_a_failing_log_write_never_breaks_the_call(monkeypatch):
    def boom(**kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(db_mod, "log_llm_call", boom)
    client = _client(_msg())

    def run():
        llm_util.set_llm_context("rid-d")
        return llm_util.logged_create(client, "analysis", model="m")

    assert _isolated(run) is client.messages._result


def test_missing_usage_is_tolerated(rows):
    client = _client(SimpleNamespace(content=[]))

    def run():
        llm_util.set_llm_context("rid-e")
        llm_util.logged_create(client, "analysis", model="m")

    _isolated(run)
    assert rows[0]["input_tokens"] is None and rows[0]["output_tokens"] is None and rows[0]["status"] == "ok"


def test_raw_ocr_call_is_recorded_with_its_own_call_site(rows):
    def run():
        llm_util.set_llm_context("rid-f", path="screenshot", n_images=3)
        llm_util.record_raw_call("ocr", "vision-m", {"usage": {"input_tokens": 100, "output_tokens": 20}}, 42)
        llm_util.record_raw_call("ocr", "vision-m", None, 5, status="error")

    _isolated(run)
    assert [r["call_site"] for r in rows] == ["ocr", "ocr"]
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"], rows[0]["latency_ms"]) == (100, 20, 42)
    assert rows[1]["status"] == "error" and rows[1]["input_tokens"] is None


def test_context_survives_the_threadpool():
    from starlette.concurrency import run_in_threadpool

    async def go():
        llm_util.set_llm_context("rid-g", path="paste", n_images=0)
        return await run_in_threadpool(llm_util.current_llm_context)

    ctx = _isolated(lambda: asyncio.run(go()))
    assert ctx and ctx["request_id"] == "rid-g"


def test_lane_is_backfilled_once_known(monkeypatch):
    calls = []
    monkeypatch.setattr(db_mod, "set_llm_call_lane", lambda rid, lane: calls.append((rid, lane)))

    def run():
        llm_util.finalize_llm_context("FRAUD")          # no context yet: nothing happens
        llm_util.set_llm_context("rid-h")
        llm_util.finalize_llm_context(None)             # lane unknown: nothing happens
        llm_util.finalize_llm_context("FRAUD")

    _isolated(run)
    assert calls == [("rid-h", "FRAUD")]


def test_db_writers_do_not_raise_without_a_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db_mod.log_llm_call(request_id="x", call_site="analysis") is None
    assert db_mod.set_llm_call_lane("x", "FRAUD") is None


def test_table_has_no_content_columns():
    import inspect
    src = inspect.getsource(db_mod.init_db)
    block = src.split("CREATE TABLE IF NOT EXISTS llm_call_log", 1)[1].split(")", 1)[0]
    assert "request_id" in block and "input_tokens" in block
    for banned in ("conversation_text", "text", "prompt", "content", "email"):
        assert banned not in block, banned


def test_no_direct_ai_calls_outside_the_wrapper():
    # llm_util.py holds the wrapper; llm_health.py is the health probe (its own short timeout,
    # excluded from cost/latency stats). Every other AI call must go through logged_create / record_raw_call.
    pat = re.compile(r"\.messages\.create\b")
    for path in sorted((ROOT / "app").glob("*.py")):
        if path.name in ("llm_util.py", "llm_health.py"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if pat.search(line) and not line.lstrip().startswith("#"):
                pytest.fail(f"{path.name}: direct AI call: {line.strip()}")
