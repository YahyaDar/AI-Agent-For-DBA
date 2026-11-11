import time

import psycopg
import pytest

from conftest import LAB_PRIMARY_DSN, LAB_REPLICA_DSN
from pgagent import healer
from pgagent.audit import store
from pgagent.config import settings
from pgagent.executor import verify_postcondition
from pgagent.policy import evaluate
from pgagent.schemas import (
    ActionId,
    ExecutionResult,
    Finding,
    FindingType,
    ProposedAction,
    Verdict,
)
from pgagent.tools.db import ro_conn, store_conn


def failed(d):
    return {c.name for c in d.checks if not c.passed}


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, handler):
        self._handler = handler

    def execute(self, sql, params=()):
        return _FakeCursor(self._handler(sql, params))


def _zero_store():
    return _FakeConn(lambda sql, params: [{"n": 0}])


def table_case(action_id, ftype, schema, table, target=None):
    f = Finding(type=ftype, severity="warning", target=target or f"{schema}.{table}", evidence={})
    a = ProposedAction(
        finding_id=f.id,
        action_id=action_id,
        params={"schema_name": schema, "table": table},
        proposed_by="llm",
    )
    return a, f


@pytest.fixture
def ro():
    try:
        c = ro_conn()
    except psycopg.OperationalError:
        pytest.skip("lab not running")
    yield c
    c.close()


@pytest.fixture(scope="module")
def guard_objects():
    try:
        c = psycopg.connect(LAB_PRIMARY_DSN, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("lab not running")
    c.execute("DROP TABLE IF EXISTS public.guard_tbl")
    c.execute("CREATE TABLE public.guard_tbl (id int PRIMARY KEY)")
    c.execute("INSERT INTO public.guard_tbl VALUES (1)")
    yield
    c.execute("DROP TABLE IF EXISTS public.guard_tbl")
    c.close()


# ---------- Pure unit tests ----------
@pytest.mark.pure
def test_unit_seen_recently_excludes_skipped_dry_run():
    captured_sql = []

    def store_handler(sql, params):
        captured_sql.append(sql)
        return []

    f = Finding(type=FindingType.TABLE_BLOAT, severity="warning", target="public.x", evidence={})
    assert store.seen_recently(_FakeConn(store_handler), f, 120) is False
    assert "skipped" in captured_sql[0]


@pytest.mark.pure
@pytest.mark.parametrize(
    "blocker_kind,expected_substr",
    [
        ("open_tx", "open transaction"),
        ("prepared_xact", "prepared transaction"),
        ("repl_slot", "replication slot"),
    ],
)
def test_unit_vacuum_futility_escalates_when_xmin_pinned(blocker_kind, expected_substr):
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "x")

    def ro_handler(sql, params):
        if "pg_class" in sql:
            return [{"relkind": "r", "bytes": 8192}]
        if "pg_stat_activity" in sql and blocker_kind == "open_tx":
            return [
                {
                    "pid": 4242,
                    "state": "idle in transaction",
                    "backend_xid": "500",
                    "backend_xmin": None,
                }
            ]
        if "pg_prepared_xacts" in sql and blocker_kind == "prepared_xact":
            return [{"gid": "prep_1", "xid": "501"}]
        if "pg_replication_slots" in sql and blocker_kind == "repl_slot":
            return [{"slot_name": "stale_slot", "active": False, "xmin": None, "catalog_xmin": "502"}]
        return []

    decision = evaluate(a, f, _FakeConn(ro_handler), store=_zero_store())
    assert decision.verdict == Verdict.ESCALATE
    assert any(expected_substr in r for r in decision.reasons)


@pytest.mark.pure
def test_unit_heavy_maintenance_denied_when_connection_load_exceeds_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "load_guard_max_conn_ratio", 0.5)
    a, f = table_case(ActionId.REINDEX_CONCURRENTLY, FindingType.INDEX_BLOAT, "public", "x")

    def ro_handler(sql, params):
        if "pg_class" in sql:
            return [{"relkind": "r", "bytes": 8192}]
        if "max_connections" in sql:
            return [{"active_conns": 80, "max_conns": 100}]
        if "pg_stat_replication" in sql:
            return [{"max_lag_bytes": 0}]
        return []

    decision = evaluate(a, f, _FakeConn(ro_handler), store=_zero_store())
    assert decision.verdict == Verdict.DENY
    assert any("load_guard_connections" in r for r in decision.reasons)


@pytest.mark.pure
def test_unit_heavy_maintenance_denied_when_replication_lag_exceeds_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "load_guard_max_repl_lag_bytes", 1024)
    a, f = table_case(ActionId.REINDEX_CONCURRENTLY, FindingType.INDEX_BLOAT, "public", "x")

    def ro_handler(sql, params):
        if "pg_class" in sql:
            return [{"relkind": "r", "bytes": 8192}]
        if "max_connections" in sql:
            return [{"active_conns": 5, "max_conns": 100}]
        if "pg_stat_replication" in sql:
            return [{"max_lag_bytes": 4096}]
        return []

    decision = evaluate(a, f, _FakeConn(ro_handler), store=_zero_store())
    assert decision.verdict == Verdict.DENY
    assert any("load_guard_replication_lag" in r for r in decision.reasons)


@pytest.mark.pure
def test_unit_per_action_rate_limit_denies_when_action_quota_exhausted(monkeypatch):
    monkeypatch.setattr(settings, "max_actions_per_hour", 20)
    monkeypatch.setattr(
        settings, "per_action_max_per_hour", {ActionId.ANALYZE_TABLE.value: 2}
    )
    a, f = table_case(ActionId.ANALYZE_TABLE, FindingType.STALE_STATS, "public", "x")

    def ro_handler(sql, params):
        if "pg_class" in sql:
            return [{"relkind": "r", "bytes": 8192}]
        return []

    def store_handler(sql, params):
        return [{"n": 2}]

    decision = evaluate(a, f, _FakeConn(ro_handler), store=_FakeConn(store_handler))
    assert decision.verdict == Verdict.DENY
    assert failed(decision) == {"action_rate_limit"}


@pytest.mark.pure
def test_unit_verify_postcondition_detects_unimproved_and_improved_outcomes():
    ok = ExecutionResult(status="success", sql_executed="VACUUM x", duration_ms=10)

    a_vac, _ = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "x")
    res_vac_fail = verify_postcondition(a_vac, {"dead": 50_000}, {"dead": 50_000}, ok)
    assert res_vac_fail.status == "failed" and "dead tuples did not decrease" in (res_vac_fail.error or "")

    res_vac_ok = verify_postcondition(a_vac, {"dead": 50_000}, {"dead": 100}, ok)
    assert res_vac_ok.status == "success"

    a_an, _ = table_case(ActionId.ANALYZE_TABLE, FindingType.STALE_STATS, "public", "x")
    res_an_fail = verify_postcondition(
        a_an,
        {"last_analyze": "2026-01-01T00:00:00Z", "mod_since_analyze": 2000},
        {"last_analyze": "2026-01-01T00:00:00Z", "mod_since_analyze": 2000},
        ok,
    )
    assert res_an_fail.status == "failed" and "last_analyze did not advance" in (res_an_fail.error or "")

    res_an_ok = verify_postcondition(
        a_an,
        {"last_analyze": "2026-01-01T00:00:00Z", "mod_since_analyze": 2000},
        {"last_analyze": "2026-01-01T00:01:00Z", "mod_since_analyze": 0},
        ok,
    )
    assert res_an_ok.status == "success"

    f_pid = Finding(type=FindingType.IDLE_IN_TXN, severity="warning", target="pid:123", evidence={"pid": 123})
    a_kill = ProposedAction(
        finding_id=f_pid.id, action_id=ActionId.TERMINATE_BACKEND, params={"pid": 123}, proposed_by="rules"
    )
    res_kill_fail = verify_postcondition(
        a_kill,
        {"exists": True, "state": "idle in transaction"},
        {"exists": True, "state": "idle in transaction"},
        ok,
    )
    assert res_kill_fail.status == "failed" and "still exists in state" in (res_kill_fail.error or "")

    res_kill_ok = verify_postcondition(
        a_kill,
        {"exists": True, "state": "idle in transaction"},
        {"exists": False, "state": None},
        ok,
    )
    assert res_kill_ok.status == "success"


# ---------- Live lab tests ----------
@pytest.mark.lab
def test_dry_run_records_skipped_without_triggering_cooldown(ro, guard_objects, monkeypatch):
    monkeypatch.setattr(settings, "cooldown_s", 120)
    monkeypatch.setattr(settings, "dry_run", True)
    f = Finding(
        type=FindingType.STALE_STATS,
        severity="warning",
        target="public.guard_tbl",
        evidence={"live": 1, "mod_since_analyze": 1000},
    )
    with store_conn() as sconn:
        healer.handle(f, ro, sconn)
        recent = store.list_recent(sconn, 1)
        assert len(recent) == 1 and recent[0]["status"] == "skipped"
        assert store.seen_recently(sconn, f, settings.cooldown_s) is False

        monkeypatch.setattr(settings, "dry_run", False)
        healer.handle(f, ro, sconn)
        recent = store.list_recent(sconn, 2)
        assert recent[0]["status"] == "success"
        assert store.seen_recently(sconn, f, settings.cooldown_s) is True


@pytest.mark.lab
def test_vacuum_futility_escalates_on_open_transaction(ro, guard_objects):
    with psycopg.connect(LAB_PRIMARY_DSN, autocommit=False, application_name="guard_idle") as c:
        c.execute("SELECT txid_current()")
        try:
            a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "guard_tbl")
            d = evaluate(a, f, ro)
            assert d.verdict == Verdict.ESCALATE
            assert failed(d) == {"vacuum_futility"}
            assert any("open transaction" in r for r in d.reasons)
        finally:
            c.rollback()


@pytest.mark.lab
def test_vacuum_futility_escalates_on_prepared_transaction(ro, guard_objects):
    max_prep = int(ro.execute("SHOW max_prepared_transactions").fetchone()["max_prepared_transactions"])
    if max_prep > 0:
        with psycopg.connect(LAB_PRIMARY_DSN, autocommit=False) as c:
            c.execute("UPDATE public.guard_tbl SET id = id WHERE id = 1")
            c.execute("PREPARE TRANSACTION 'guard_prep_futility'")
        try:
            a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "guard_tbl")
            d = evaluate(a, f, ro)
            assert d.verdict == Verdict.ESCALATE
            assert failed(d) == {"vacuum_futility"}
            assert any("prepared transaction" in r for r in d.reasons)
        finally:
            with psycopg.connect(LAB_PRIMARY_DSN, autocommit=True) as admin:
                admin.execute("ROLLBACK PREPARED 'guard_prep_futility'")
    else:
        class _PreparedXactProxy:
            def execute(self, sql, params=None):
                cur = ro.execute(sql, params) if params is not None else ro.execute(sql)
                if "pg_prepared_xacts" in sql:
                    class _Res:
                        def fetchall(self_inner):
                            return [{"gid": "guard_prep_futility", "xid": "777"}]
                    return _Res()
                return cur

        a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "guard_tbl")
        d = evaluate(a, f, _PreparedXactProxy())
        assert d.verdict == Verdict.ESCALATE
        assert failed(d) == {"vacuum_futility"}
        assert any("prepared transaction" in r for r in d.reasons)


@pytest.mark.lab
def test_vacuum_futility_escalates_on_inactive_replication_slot(ro, guard_objects):
    with psycopg.connect(LAB_PRIMARY_DSN, autocommit=True) as admin:
        admin.execute("SELECT pg_create_physical_replication_slot('guard_futility_slot')")
        try:
            a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "guard_tbl")
            d = evaluate(a, f, ro)
            assert d.verdict == Verdict.ESCALATE
            assert failed(d) == {"vacuum_futility"}
            assert any("replication slot" in r for r in d.reasons)
        finally:
            admin.execute("SELECT pg_drop_replication_slot('guard_futility_slot')")


@pytest.mark.lab
def test_load_guard_denies_reindex_when_connections_saturated(ro, guard_objects, monkeypatch):
    monkeypatch.setattr(settings, "load_guard_max_conn_ratio", 0.001)
    a, f = table_case(ActionId.REINDEX_CONCURRENTLY, FindingType.INDEX_BLOAT, "public", "guard_tbl")
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY
    assert failed(d) == {"load_guard_connections"}


@pytest.mark.lab
def test_load_guard_denies_reindex_when_replication_lag_high(ro, guard_objects, monkeypatch):
    monkeypatch.setattr(settings, "load_guard_max_repl_lag_bytes", 100)
    with (
        psycopg.connect(LAB_REPLICA_DSN, autocommit=True) as rep,
        psycopg.connect(LAB_PRIMARY_DSN, autocommit=True) as prim,
    ):
        rep.execute("SELECT pg_wal_replay_pause()")
        try:
            prim.execute(
                "INSERT INTO public.guard_tbl SELECT g FROM generate_series(2, 500) g ON CONFLICT DO NOTHING"
            )
            prim.execute("DELETE FROM public.guard_tbl WHERE id > 1")
            a, f = table_case(ActionId.REINDEX_CONCURRENTLY, FindingType.INDEX_BLOAT, "public", "guard_tbl")
            d = evaluate(a, f, ro)
            assert d.verdict == Verdict.DENY
            assert failed(d) == {"load_guard_replication_lag"}
        finally:
            rep.execute("SELECT pg_wal_replay_resume()")
            end = time.time() + 10
            while time.time() < end:
                lag = ro.execute(
                    "SELECT COALESCE(max(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn))::bigint, 0) AS lag "
                    "FROM pg_stat_replication"
                ).fetchone()["lag"]
                if lag == 0:
                    break
                time.sleep(0.1)


@pytest.mark.lab
def test_per_action_rate_limit_enforced_in_agent_store(ro, guard_objects, monkeypatch):
    monkeypatch.setattr(settings, "max_actions_per_hour", 20)
    monkeypatch.setattr(
        settings,
        "per_action_max_per_hour",
        {ActionId.ANALYZE_TABLE.value: 1, ActionId.VACUUM_TABLE.value: 5},
    )
    f_analyze = Finding(
        type=FindingType.STALE_STATS,
        severity="warning",
        target="public.guard_tbl",
        evidence={"live": 1, "mod_since_analyze": 1000},
    )
    with store_conn() as sconn:
        healer.handle(f_analyze, ro, sconn)
        assert store.count_executed_last_hour(sconn, ActionId.ANALYZE_TABLE.value) == 1
        assert store.count_executed_last_hour(sconn, ActionId.VACUUM_TABLE.value) == 0

        a_analyze, f_analyze_2 = table_case(
            ActionId.ANALYZE_TABLE, FindingType.STALE_STATS, "public", "guard_tbl"
        )
        d_analyze = evaluate(a_analyze, f_analyze_2, ro, store=sconn)
        assert d_analyze.verdict == Verdict.DENY
        assert failed(d_analyze) == {"action_rate_limit"}

        a_vacuum, f_vacuum = table_case(
            ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "guard_tbl"
        )
        d_vacuum = evaluate(a_vacuum, f_vacuum, ro, store=sconn)
        assert d_vacuum.verdict == Verdict.ALLOW


@pytest.mark.lab
def test_postcondition_verification_marks_unimproved_execution_in_audit(ro, guard_objects, monkeypatch):
    records = []
    monkeypatch.setattr(store, "record", records.append)
    monkeypatch.setattr(
        healer,
        "snapshot",
        lambda action, conn: {"live": 100, "dead": 50_000, "mod_since_analyze": 0, "last_vacuum": None, "last_analyze": None},
    )
    f = Finding(
        type=FindingType.TABLE_BLOAT,
        severity="warning",
        target="public.guard_tbl",
        evidence={"live": 100, "dead": 50_000, "dead_ratio": 0.99},
    )
    healer.handle(f, ro)
    assert len(records) == 1
    rec = records[0]
    assert rec.decision.verdict == Verdict.ALLOW
    assert rec.result is not None
    assert rec.result.status == "failed"
    assert "postcondition failed" in (rec.result.error or "")
