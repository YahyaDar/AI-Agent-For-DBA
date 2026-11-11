import pytest

from pgagent.config import settings
from pgagent.policy.catalog import CATALOG, TableParams
from pgagent.schemas import ActionId, Finding, FindingType, RiskTier

pytestmark = pytest.mark.pure


def test_schema_and_catalog_entries_for_wraparound_and_index_bloat():
    assert FindingType.XID_WRAPAROUND == "xid_wraparound"
    assert ActionId.VACUUM_FREEZE == "vacuum_freeze"

    spec = CATALOG[ActionId.VACUUM_FREEZE]
    assert spec.tier == RiskTier.MEDIUM
    assert spec.params is TableParams
    assert spec.finding_type == FindingType.XID_WRAPAROUND

    reindex_spec = CATALOG[ActionId.REINDEX_CONCURRENTLY]
    assert reindex_spec.tier == RiskTier.MEDIUM
    assert reindex_spec.params is TableParams
    assert reindex_spec.finding_type == FindingType.INDEX_BLOAT


def test_config_thresholds_for_xid_and_index_bloat():
    assert hasattr(settings, "xid_age_warn")
    assert hasattr(settings, "xid_age_emergency")
    assert hasattr(settings, "index_bloat_ratio")
    assert hasattr(settings, "index_bloat_min_bytes")
    assert settings.xid_age_warn < settings.xid_age_emergency
    assert "vacuum_freeze" in settings.per_action_max_per_hour


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


def test_vacuum_freeze_allowed_when_healthy():
    from pgagent.policy import evaluate
    from pgagent.schemas import Finding, ProposedAction, Verdict

    f = Finding(type=FindingType.XID_WRAPAROUND, severity="warning", target="public.x", evidence={"xid_age": 120_000_000})
    a = ProposedAction(
        finding_id=f.id,
        action_id=ActionId.VACUUM_FREEZE,
        proposed_by="rules",
        params={"schema_name": "public", "table": "x"},
    )

    def ro_handler(sql, params):
        if "pg_class" in sql and "pg_namespace" in sql:
            return [{"relkind": "r", "bytes": 100_000, "xid_age": 120_000_000}]
        if "max_connections" in sql:
            return [{"active_conns": 5, "max_conns": 100}]
        if "pg_stat_replication" in sql:
            return [{"max_lag_bytes": 0}]
        return []

    d = evaluate(a, f, _FakeConn(ro_handler), store=_zero_store())
    assert d.verdict == Verdict.ALLOW


def test_vacuum_freeze_escalates_on_emergency_ceiling(monkeypatch):
    from pgagent.policy import evaluate
    from pgagent.schemas import Finding, ProposedAction, Verdict

    monkeypatch.setattr(settings, "xid_age_emergency", 200_000_000)
    f = Finding(type=FindingType.XID_WRAPAROUND, severity="critical", target="public.x", evidence={"xid_age": 200_500_000})
    a = ProposedAction(
        finding_id=f.id,
        action_id=ActionId.VACUUM_FREEZE,
        proposed_by="rules",
        params={"schema_name": "public", "table": "x"},
    )

    def ro_handler(sql, params):
        if "pg_class" in sql and "pg_namespace" in sql:
            return [{"relkind": "r", "bytes": 100_000, "xid_age": 200_500_000}]
        if "max_connections" in sql:
            return [{"active_conns": 5, "max_conns": 100}]
        if "pg_stat_replication" in sql:
            return [{"max_lag_bytes": 0}]
        return []

    d = evaluate(a, f, _FakeConn(ro_handler), store=_zero_store())
    assert d.verdict == Verdict.ESCALATE
    assert "xid_emergency_ceiling" in [c.name for c in d.checks if not c.passed]
    assert any("emergency ceiling" in r for r in d.reasons)


def test_vacuum_freeze_escalates_on_vacuum_futility():
    from pgagent.policy import evaluate
    from pgagent.schemas import Finding, ProposedAction, Verdict

    f = Finding(type=FindingType.XID_WRAPAROUND, severity="warning", target="public.x", evidence={"xid_age": 120_000_000})
    a = ProposedAction(
        finding_id=f.id,
        action_id=ActionId.VACUUM_FREEZE,
        proposed_by="rules",
        params={"schema_name": "public", "table": "x"},
    )

    def ro_handler(sql, params):
        if "pg_class" in sql and "pg_namespace" in sql:
            return [{"relkind": "r", "bytes": 100_000, "xid_age": 120_000_000}]
        if "max_connections" in sql:
            return [{"active_conns": 5, "max_conns": 100}]
        if "pg_stat_replication" in sql:
            return [{"max_lag_bytes": 0}]
        if "pg_stat_activity" in sql:
            return [{"pid": 99, "state": "idle in transaction", "backend_xid": "100", "backend_xmin": None, "xid_age": 50}]
        return []

    d = evaluate(a, f, _FakeConn(ro_handler), store=_zero_store())
    assert d.verdict == Verdict.ESCALATE
    assert "vacuum_futility" in [c.name for c in d.checks if not c.passed]


def test_executor_sql_and_ast_validation_for_freeze_and_reindex():
    from pgagent.executor.actions import build_sql, validate_ast
    from pgagent.schemas import ProposedAction

    class _DummyConn:
        def __init__(self):
            pass

    a_freeze = ProposedAction(
        finding_id=Finding(type=FindingType.XID_WRAPAROUND, severity="warning", target="public.x", evidence={}).id,
        action_id=ActionId.VACUUM_FREEZE,
        proposed_by="rules",
        params={"schema_name": "public", "table": "x"},
    )
    sql_freeze = build_sql(a_freeze, None)
    assert sql_freeze == 'VACUUM (FREEZE) "public"."x"'
    validate_ast(sql_freeze, ActionId.VACUUM_FREEZE)

    a_reindex = ProposedAction(
        finding_id=Finding(type=FindingType.INDEX_BLOAT, severity="warning", target="public.x", evidence={}).id,
        action_id=ActionId.REINDEX_CONCURRENTLY,
        proposed_by="rules",
        params={"schema_name": "public", "table": "x"},
    )
    sql_reindex = build_sql(a_reindex, None)
    assert sql_reindex == 'REINDEX TABLE CONCURRENTLY "public"."x"'
    validate_ast(sql_reindex, ActionId.REINDEX_CONCURRENTLY)


def test_verify_postcondition_for_vacuum_freeze():
    from pgagent.executor import verify_postcondition
    from pgagent.schemas import ExecutionResult, ProposedAction

    a = ProposedAction(
        finding_id=Finding(type=FindingType.XID_WRAPAROUND, severity="warning", target="public.x", evidence={}).id,
        action_id=ActionId.VACUUM_FREEZE,
        proposed_by="rules",
        params={"schema_name": "public", "table": "x"},
    )
    ok = ExecutionResult(status="success", sql_executed="VACUUM (FREEZE) x", duration_ms=50)

    # Failed: age did not decrease
    res_fail = verify_postcondition(a, {"xid_age": 150_000_000}, {"xid_age": 150_000_000}, ok)
    assert res_fail.status == "failed"
    assert "relfrozenxid age did not decrease" in (res_fail.error or "")

    # Success: age decreased
    res_ok = verify_postcondition(a, {"xid_age": 150_000_000}, {"xid_age": 0}, ok)
    assert res_ok.status == "success"


def test_verify_postcondition_for_reindex_concurrently():
    from pgagent.executor import verify_postcondition
    from pgagent.schemas import ExecutionResult, ProposedAction

    a = ProposedAction(
        finding_id=Finding(type=FindingType.INDEX_BLOAT, severity="warning", target="public.x", evidence={}).id,
        action_id=ActionId.REINDEX_CONCURRENTLY,
        proposed_by="rules",
        params={"schema_name": "public", "table": "x"},
    )
    ok = ExecutionResult(status="success", sql_executed="REINDEX TABLE CONCURRENTLY x", duration_ms=50)

    # Failed: invalid indexes remain
    res_fail = verify_postcondition(a, {"invalid_indexes": 1}, {"invalid_indexes": 1}, ok)
    assert res_fail.status == "failed"
    assert "invalid index(es) remain" in (res_fail.error or "")

    # Success: invalid indexes fixed
    res_ok = verify_postcondition(a, {"invalid_indexes": 1}, {"invalid_indexes": 0}, ok)
    assert res_ok.status == "success"


def test_detect_xid_wraparound(monkeypatch):
    from pgagent.checks.health import check_tables

    monkeypatch.setattr(settings, "xid_age_warn", 100_000_000)
    monkeypatch.setattr(settings, "xid_age_emergency", 200_000_000)

    def fake_handler(sql, params=()):
        return [
            {
                "schema": "public",
                "table": "warn_tbl",
                "live": 1000,
                "dead": 0,
                "mod_since_analyze": 0,
                "xid_age": 150_000_000,
            },
            {
                "schema": "public",
                "table": "emerg_tbl",
                "live": 1000,
                "dead": 0,
                "mod_since_analyze": 0,
                "xid_age": 205_000_000,
            },
            {
                "schema": "public",
                "table": "ok_tbl",
                "live": 1000,
                "dead": 0,
                "mod_since_analyze": 0,
                "xid_age": 500,
            },
        ]

    findings = check_tables(_FakeConn(fake_handler))
    xid_findings = [f for f in findings if f.type == FindingType.XID_WRAPAROUND]
    assert len(xid_findings) == 2
    f_warn = next(f for f in xid_findings if f.target == "public.warn_tbl")
    assert f_warn.severity == "warning"
    assert f_warn.evidence["xid_age"] == 150_000_000

    f_emerg = next(f for f in xid_findings if f.target == "public.emerg_tbl")
    assert f_emerg.severity == "critical"
    assert f_emerg.evidence["xid_age"] == 205_000_000


def test_detect_index_bloat_and_invalid_index(monkeypatch):
    from pgagent.checks.health import check_indexes

    monkeypatch.setattr(settings, "index_bloat_ratio", 0.3)
    monkeypatch.setattr(settings, "index_bloat_min_bytes", 1_000_000)

    def fake_handler(sql, params=()):
        if "pg_index" in sql:
            return [
                {
                    "schema": "public",
                    "table": "inv_tbl",
                    "index_name": "idx_inv",
                    "index_oid": 11,
                    "index_bytes": 2_000_000,
                    "is_valid": False,
                    "amname": "btree",
                },
                {
                    "schema": "public",
                    "table": "bloat_tbl",
                    "index_name": "idx_bloat",
                    "index_oid": 12,
                    "index_bytes": 5_000_000,
                    "is_valid": True,
                    "amname": "btree",
                },
                {
                    "schema": "public",
                    "table": "healthy_tbl",
                    "index_name": "idx_healthy",
                    "index_oid": 13,
                    "index_bytes": 5_000_000,
                    "is_valid": True,
                    "amname": "btree",
                },
            ]
        if "pgstatindex" in sql:
            oid = params[0] if params else 0
            if oid == 12:
                # low density -> 80% bloat
                return [{"avg_leaf_density": 20.0}]
            return [{"avg_leaf_density": 90.0}]
        return []

    findings = check_indexes(_FakeConn(fake_handler))
    assert len(findings) == 2
    f_inv = next(f for f in findings if f.target == "public.inv_tbl")
    assert f_inv.type == FindingType.INDEX_BLOAT
    assert f_inv.severity == "critical"

    f_bloat = next(f for f in findings if f.target == "public.bloat_tbl")
    assert f_bloat.type == FindingType.INDEX_BLOAT
    assert f_bloat.evidence["bloat_ratio"] == 0.8


def test_healer_propose_for_wraparound_and_index_bloat():
    from pgagent.healer import propose

    f_xid = Finding(type=FindingType.XID_WRAPAROUND, severity="warning", target="public.my_table", evidence={})
    p_xid = propose(f_xid)
    assert p_xid is not None
    assert p_xid.action_id == ActionId.VACUUM_FREEZE
    assert p_xid.params == {"schema_name": "public", "table": "my_table"}

    f_idx = Finding(type=FindingType.INDEX_BLOAT, severity="warning", target="public.my_table", evidence={})
    p_idx = propose(f_idx)
    assert p_idx is not None
    assert p_idx.action_id == ActionId.REINDEX_CONCURRENTLY
    assert p_idx.params == {"schema_name": "public", "table": "my_table"}


# ---------- Live lab integration & adversarial tests ----------
def _wait_for(cond, what, timeout=10, interval=0.1):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for: {what}")


@pytest.fixture
def ro():
    import psycopg

    from pgagent.tools.db import ro_conn
    try:
        c = ro_conn()
    except psycopg.OperationalError:
        pytest.skip("lab not running")
    yield c
    c.close()


@pytest.mark.lab
def test_healer_fixes_xid_wraparound(ro, monkeypatch):
    import psycopg

    from conftest import LAB_PRIMARY_DSN
    from pgagent import healer
    from scenarios import xid_wraparound
    from scenarios.common import kill_scenario_sessions

    monkeypatch.setattr(settings, "cooldown_s", 0)
    kill_scenario_sessions()
    xid_wraparound.inject()

    with psycopg.connect(LAB_PRIMARY_DSN) as admin:
        age_before = admin.execute(
            "SELECT age(relfrozenxid)::bigint FROM pg_class WHERE oid = 'public.xid_test'::regclass"
        ).fetchone()[0]
        assert age_before >= settings.xid_age_warn

    healer.run_cycle()

    with psycopg.connect(LAB_PRIMARY_DSN) as admin:
        _wait_for(
            lambda: admin.execute(
                "SELECT age(relfrozenxid)::bigint FROM pg_class WHERE oid = 'public.xid_test'::regclass"
            ).fetchone()[0]
            < settings.xid_age_warn,
            "vacuum_freeze to reset relfrozenxid",
        )
        age_after = admin.execute(
            "SELECT age(relfrozenxid)::bigint FROM pg_class WHERE oid = 'public.xid_test'::regclass"
        ).fetchone()[0]
        assert age_after < 1000

    xid_wraparound.cleanup()


@pytest.mark.lab
def test_healer_escalates_on_emergency_xid_ceiling(ro, monkeypatch):
    from pgagent import healer
    from pgagent.audit import store
    from pgagent.schemas import Verdict
    from pgagent.tools.db import store_conn
    from scenarios.common import connect, drop_tables, kill_scenario_sessions

    monkeypatch.setattr(settings, "cooldown_s", 0)
    monkeypatch.setattr(settings, "xid_age_emergency", 200_000_000)
    kill_scenario_sessions()

    tbl = "public.xid_emerg_test"
    with connect() as c:
        c.execute(f"DROP TABLE IF EXISTS {tbl}")
        c.execute(f"CREATE TABLE {tbl} (id int PRIMARY KEY)")
        c.execute(f"ALTER TABLE {tbl} SET (autovacuum_enabled = false)")
        c.execute(f"INSERT INTO {tbl} VALUES (1)")
        c.execute(f"VACUUM (FREEZE) {tbl}")
        c.execute(
            f"UPDATE pg_class SET relfrozenxid = ((txid_current() - 205000000) & 4294967295)::text::xid "
            f"WHERE oid = '{tbl}'::regclass"
        )

    try:
        with store_conn() as sconn:
            healer.run_cycle()
            recent = store.list_recent(sconn, 5)
            emerg_records = [
                r for r in recent
                if r["target"] == tbl and r["finding_type"] == "xid_wraparound"
            ]
            assert len(emerg_records) >= 1
            rec = emerg_records[0]
            assert rec["verdict"] == Verdict.ESCALATE.value
            assert any("emergency ceiling" in str(re) for re in (rec["reasons"] or []))

        with connect() as admin:
            age_still_high = admin.execute(
                f"SELECT age(relfrozenxid)::bigint FROM pg_class WHERE oid = '{tbl}'::regclass"
            ).fetchone()[0]
            assert age_still_high >= 200_000_000
    finally:
        drop_tables(tbl)


@pytest.mark.lab
def test_healer_fixes_index_bloat(ro, monkeypatch):
    import psycopg

    from conftest import LAB_PRIMARY_DSN
    from pgagent import healer
    from scenarios import index_bloat
    from scenarios.common import kill_scenario_sessions

    monkeypatch.setattr(settings, "cooldown_s", 0)
    kill_scenario_sessions()
    index_bloat.inject()

    with psycopg.connect(LAB_PRIMARY_DSN) as admin:
        density_before = admin.execute(
            "SELECT (pgstatindex('idx_bloat_test_payload')).avg_leaf_density"
        ).fetchone()[0]
        assert density_before < 40.0

    healer.run_cycle()

    with psycopg.connect(LAB_PRIMARY_DSN) as admin:
        _wait_for(
            lambda: admin.execute(
                "SELECT (pgstatindex('idx_bloat_test_payload')).avg_leaf_density"
            ).fetchone()[0]
            > 70.0,
            "reindex_concurrently to compact bloated B-tree index",
        )
        density_after = admin.execute(
            "SELECT (pgstatindex('idx_bloat_test_payload')).avg_leaf_density"
        ).fetchone()[0]
        assert density_after > 70.0

    index_bloat.cleanup()


@pytest.mark.lab
def test_healer_handles_invalid_index_and_postcondition_surfaces_unimproved(ro, monkeypatch):
    from pgagent import healer
    from pgagent.audit import store
    from pgagent.schemas import Verdict
    from pgagent.tools.db import store_conn
    from scenarios.common import connect, drop_tables, kill_scenario_sessions

    monkeypatch.setattr(settings, "cooldown_s", 0)
    kill_scenario_sessions()

    tbl = "public.idx_invalid_tbl"
    with connect() as c:
        c.execute(f"DROP TABLE IF EXISTS {tbl}")
        c.execute(f"CREATE TABLE {tbl} (id int PRIMARY KEY, v text)")
        c.execute(f"INSERT INTO {tbl} SELECT g, g::text FROM generate_series(1, 1000) g")
        c.execute(f"CREATE INDEX idx_inv_v ON {tbl} (v)")
        c.execute("UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'idx_inv_v'::regclass")

    try:
        with connect() as admin:
            is_valid_before = admin.execute(
                "SELECT indisvalid FROM pg_index WHERE indexrelid = 'idx_inv_v'::regclass"
            ).fetchone()[0]
            assert is_valid_before is False

        with store_conn() as sconn:
            healer.run_cycle()
            recent = store.list_recent(sconn, 5)
            reindex_records = [
                r for r in recent
                if r["target"] == tbl and r["action_id"] == ActionId.REINDEX_CONCURRENTLY.value
            ]
            assert len(reindex_records) >= 1
            rec = reindex_records[0]
            assert rec["verdict"] == Verdict.ALLOW.value
            # Postgres REINDEX TABLE CONCURRENTLY skips invalid indexes;
            # postcondition verification accurately detects that the invalid index remains and marks failed.
            assert rec["status"] == "failed"

        with connect() as admin:
            is_valid_after = admin.execute(
                "SELECT indisvalid FROM pg_index WHERE indexrelid = 'idx_inv_v'::regclass"
            ).fetchone()[0]
            assert is_valid_after is False
    finally:
        drop_tables(tbl)




