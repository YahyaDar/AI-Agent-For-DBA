"""Adversarial tests: try to make the policy engine approve something unsafe.
Needs the Docker lab running (skipped otherwise)."""
import threading, time
import psycopg
import pytest
from pydantic import ValidationError

from pgagent import healer
from pgagent.audit import store
from pgagent.config import settings
from pgagent.executor.actions import execute
from pgagent.policy import evaluate
from pgagent.policy.catalog import CATALOG, ActionSpec, TableParams
from pgagent.schemas import (ActionId, Finding, FindingType, ProposedAction,
                             RiskTier, Verdict)
from pgagent.tools.db import ro_conn

# ---------- helpers ----------
def failed(d):
    return {c.name for c in d.checks if not c.passed}

def pid_case(pid, ftype=FindingType.IDLE_IN_TXN, target=None):
    f = Finding(type=ftype, severity="warning", target=target or f"pid:{pid}", evidence={"pid": pid})
    a = ProposedAction(finding_id=f.id, action_id=ActionId.TERMINATE_BACKEND,
                       params={"pid": pid}, proposed_by="llm")
    return a, f

def table_case(action_id, ftype, schema, table, target=None):
    f = Finding(type=ftype, severity="warning", target=target or f"{schema}.{table}", evidence={})
    a = ProposedAction(finding_id=f.id, action_id=action_id,
                       params={"schema_name": schema, "table": table}, proposed_by="llm")
    return a, f

def wait_state(ro, pid, state, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        r = ro.execute("SELECT state FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone()
        if r and r["state"] == state:
            return
        time.sleep(0.1)
    raise AssertionError(f"pid {pid} never reached state {state!r}")

def alive(ro, pid):
    return ro.execute("SELECT 1 FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone() is not None

# ---------- fixtures ----------
@pytest.fixture
def ro():
    try:
        c = ro_conn()
    except psycopg.OperationalError:
        pytest.skip("lab not running")
    yield c
    c.close()

@pytest.fixture
def idle_session(ro):
    conns = []
    def make(app="adv_idle"):
        c = psycopg.connect(settings.primary_dsn, autocommit=False, application_name=app)
        c.execute("SELECT txid_current()")
        wait_state(ro, c.info.backend_pid, "idle in transaction")
        conns.append(c)
        return c
    yield make
    for c in conns:
        try: c.rollback(); c.close()
        except Exception: pass

@pytest.fixture
def active_session(ro):
    c = psycopg.connect(settings.primary_dsn, autocommit=True, application_name="adv_active")
    def run():
        try: c.execute("SELECT pg_sleep(60)")
        except Exception: pass
    t = threading.Thread(target=run, daemon=True); t.start()
    wait_state(ro, c.info.backend_pid, "active")
    yield c.info.backend_pid
    c.cancel(); t.join(timeout=5); c.close()

@pytest.fixture(scope="module")
def objects():
    try:
        c = psycopg.connect(settings.primary_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("lab not running")
    c.execute("DROP VIEW IF EXISTS public.adv_view")
    c.execute("DROP TABLE IF EXISTS public.adv_tbl")
    c.execute("CREATE TABLE public.adv_tbl (id int PRIMARY KEY)")
    c.execute("INSERT INTO public.adv_tbl VALUES (1)")
    c.execute("CREATE VIEW public.adv_view AS SELECT 1 AS x")
    yield
    c.execute("DROP VIEW IF EXISTS public.adv_view")
    c.execute("DROP TABLE IF EXISTS public.adv_tbl")
    c.close()

# ---------- terminate_backend: sessions that must NOT be killed ----------
def test_control_legit_idle_session_is_allowed(ro, idle_session, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    a, f = pid_case(idle_session().info.backend_pid)
    assert evaluate(a, f, ro).verdict == Verdict.ALLOW

def test_active_session_is_never_killed(ro, active_session, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)   # isolate: only state may stop it
    a, f = pid_case(active_session)
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"still_idle_in_txn"}

def test_recently_idle_session_is_spared(ro, idle_session):
    a, f = pid_case(idle_session().info.backend_pid)       # default 30s threshold, idle ~0s
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"idle_long_enough"}

def test_agent_own_session_is_protected(ro, idle_session, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    a, f = pid_case(idle_session(app="pgagent_probe").info.backend_pid)
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"not_agent_session"}

def test_background_process_is_protected(ro):
    row = ro.execute("SELECT pid FROM pg_stat_activity "
                     "WHERE backend_type <> 'client backend' AND pid IS NOT NULL LIMIT 1").fetchone()
    a, f = pid_case(row["pid"])
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and "is_client_backend" in failed(d)

def test_nonexistent_pid_denied(ro):
    a, f = pid_case(999_999_999)
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and "backend_exists" in failed(d)

def test_session_that_recovered_after_detection_is_spared(ro, idle_session, monkeypatch):
    """TOCTOU: finding says idle-in-txn, but the app committed before the action ran."""
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    c = idle_session()
    a, f = pid_case(c.info.backend_pid)
    c.commit()
    wait_state(ro, c.info.backend_pid, "idle")
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"still_idle_in_txn"}

def test_action_must_match_finding_target(ro, idle_session, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    a, f = pid_case(idle_session().info.backend_pid, target="pid:1")
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"target_matches_finding"}

def test_wrong_finding_type_cannot_justify_a_kill(ro, idle_session, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    pid = idle_session().info.backend_pid
    a, f = pid_case(pid, ftype=FindingType.TABLE_BLOAT)
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"finding_justifies_action"}

def test_rate_limit_blocks_action(ro, idle_session, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    monkeypatch.setattr(settings, "max_actions_per_hour", 0)
    a, f = pid_case(idle_session().info.backend_pid)
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"rate_limit"}

# ---------- table actions ----------
def test_control_legit_vacuum_and_reindex_allowed(ro, objects):
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "adv_tbl")
    assert evaluate(a, f, ro).verdict == Verdict.ALLOW
    a, f = table_case(ActionId.REINDEX_CONCURRENTLY, FindingType.INDEX_BLOAT, "public", "adv_tbl")
    assert evaluate(a, f, ro).verdict == Verdict.ALLOW

@pytest.mark.parametrize("schema,table", [("agent", "audit"), ("pg_catalog", "pg_class")])
def test_protected_schemas_denied(ro, schema, table):
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, schema, table)
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"schema_allowed"}

@pytest.mark.parametrize("table", ["adv_view", "does_not_exist"])
def test_non_tables_denied(ro, objects, table):
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", table)
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"is_ordinary_table"}

def test_table_action_must_match_finding_target(ro, objects):
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "adv_tbl",
                      target="public.some_other_table")
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"target_matches_finding"}

def test_wrong_finding_type_cannot_justify_vacuum(ro, objects):
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.STALE_STATS, "public", "adv_tbl")
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"finding_justifies_action"}

def test_reindex_size_limit(ro, objects, monkeypatch):
    monkeypatch.setattr(settings, "reindex_max_bytes", 0)
    a, f = table_case(ActionId.REINDEX_CONCURRENTLY, FindingType.INDEX_BLOAT, "public", "adv_tbl")
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and failed(d) == {"size_limit"}

# ---------- tier gate and default deny ----------
def test_high_risk_tier_escalates_never_allows(ro, objects, monkeypatch):
    monkeypatch.setitem(CATALOG, ActionId.VACUUM_TABLE,
                        ActionSpec(RiskTier.HIGH, TableParams, FindingType.TABLE_BLOAT))
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "adv_tbl")
    assert evaluate(a, f, ro).verdict == Verdict.ESCALATE

def test_action_missing_from_catalog_is_denied(ro, objects, monkeypatch):
    monkeypatch.delitem(CATALOG, ActionId.VACUUM_TABLE)
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "adv_tbl")
    d = evaluate(a, f, ro)
    assert d.verdict == Verdict.DENY and "default deny" in d.reasons[0]

# ---------- malformed / hostile parameters (no DB needed) ----------
@pytest.mark.parametrize("table", ["x; DROP TABLE y", 'x"y', "x y", "", "a" * 64, "t\nx", "x--c", "x'y"])
def test_hostile_table_names_denied(table):
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "x")
    a = a.model_copy(update={"params": {"schema_name": "public", "table": table}})
    d = evaluate(a, f, None)
    assert d.verdict == Verdict.DENY and "params_valid" in failed(d)

@pytest.mark.parametrize("pid", [0, -1, "1 OR 1=1", 12.5, None])
def test_hostile_pids_denied(pid):
    a, f = pid_case(1)
    a = a.model_copy(update={"params": {"pid": pid}})
    d = evaluate(a, f, None)
    assert d.verdict == Verdict.DENY and "params_valid" in failed(d)

def test_unknown_params_denied():
    a, f = table_case(ActionId.VACUUM_TABLE, FindingType.TABLE_BLOAT, "public", "x")
    a = a.model_copy(update={"params": {"schema_name": "public", "table": "x", "full": True}})
    assert evaluate(a, f, None).verdict == Verdict.DENY

# ---------- defence in depth + end to end ----------
def test_executor_rejects_bad_params_even_if_policy_is_bypassed(ro):
    a = ProposedAction(finding_id=Finding(type="table_bloat", severity="info", target="x.y", evidence={}).id,
                       action_id=ActionId.VACUUM_TABLE, proposed_by="llm",
                       params={"schema_name": "public", "table": "x; DROP TABLE y"})
    with pytest.raises(ValidationError):
        execute(a)

def test_full_cycle_leaves_active_session_alone(ro, active_session, monkeypatch):
    """Forge an idle-in-txn finding pointing at a busy session; run it through the real healer."""
    records = []
    monkeypatch.setattr(store, "record", records.append)    # keep the real audit log clean
    f = Finding(type=FindingType.IDLE_IN_TXN, severity="critical", target=f"pid:{active_session}",
                evidence={"pid": active_session})
    healer.handle(f, ro)
    assert alive(ro, active_session)
    assert records[0].decision.verdict == Verdict.DENY and records[0].result is None

def test_full_cycle_spares_fresh_idle_session(ro, idle_session, monkeypatch):
    records = []
    monkeypatch.setattr(store, "record", records.append)
    pid = idle_session().info.backend_pid
    f = Finding(type=FindingType.IDLE_IN_TXN, severity="critical", target=f"pid:{pid}",
                evidence={"pid": pid})
    healer.handle(f, ro)
    assert alive(ro, pid)
    assert records[0].decision.verdict == Verdict.DENY
