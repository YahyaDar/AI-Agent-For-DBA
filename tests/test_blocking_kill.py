"""Blocking-aware kill. Needs the Docker lab running (skipped otherwise)."""
import threading, time
import psycopg
import pytest

from pgagent import healer
from pgagent.audit import store
from pgagent.config import settings
from pgagent.policy import evaluate
from pgagent.schemas import ActionId, Finding, FindingType, ProposedAction, Verdict
from pgagent.tools.db import ro_conn

TABLE = "public.blk_test"

@pytest.fixture
def ro():
    try:
        c = ro_conn()
    except psycopg.OperationalError:
        pytest.skip("lab not running")
    yield c
    c.close()

@pytest.fixture
def table(ro):
    c = psycopg.connect(settings.primary_dsn, autocommit=True)
    c.execute(f"DROP TABLE IF EXISTS {TABLE}")
    c.execute(f"CREATE TABLE {TABLE} (id int PRIMARY KEY, v int)")
    c.execute(f"INSERT INTO {TABLE} VALUES (1, 0)")
    yield
    c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name LIKE 'blk_%'")
    time.sleep(0.3)
    c.execute(f"DROP TABLE IF EXISTS {TABLE}")
    c.close()

def wait_for(cond, what, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for: {what}")

def idle_pid_state(ro, pid):
    r = ro.execute("SELECT state FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone()
    return r and r["state"]

def make_blocker(ro, n_waiters):
    holder = psycopg.connect(settings.primary_dsn, autocommit=False, application_name="blk_holder")
    holder.execute(f"UPDATE {TABLE} SET v = v + 1 WHERE id = 1")      # row lock held, txn left open
    pid = holder.info.backend_pid
    wait_for(lambda: idle_pid_state(ro, pid) == "idle in transaction", "holder idle in txn")
    done = []
    def waiter(i):
        c = psycopg.connect(settings.primary_dsn, autocommit=False, application_name=f"blk_waiter_{i}")
        try:
            c.execute(f"UPDATE {TABLE} SET v = v + 1 WHERE id = 1")   # blocks until holder is gone
            c.commit(); done.append(i)
        except Exception as e:
            done.append(f"err: {e}")
        finally:
            c.close()
    threads = [threading.Thread(target=waiter, args=(i,), daemon=True) for i in range(n_waiters)]
    for t in threads: t.start()
    wait_for(lambda: ro.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type='Lock' "
                                "AND application_name LIKE 'blk_waiter%'").fetchone()["n"] == n_waiters,
             "waiters blocked")
    return holder, pid, threads, done

def test_blocking_idle_session_is_killed_and_waiters_proceed(ro, table, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    monkeypatch.setattr(settings, "kill_requires_blocking", True)
    records = []
    monkeypatch.setattr(store, "record", records.append)       # keep the real audit log clean

    holder, pid, threads, done = make_blocker(ro, 2)
    f = Finding(type=FindingType.IDLE_IN_TXN, severity="critical", target=f"pid:{pid}", evidence={"pid": pid})
    healer.handle(f, ro)
    for t in threads: t.join(timeout=15)

    rec = records[0]
    assert rec.decision.verdict == Verdict.ALLOW and rec.result.status == "success"
    assert rec.before_metrics["blocked_waiters"] == 2          # victim record: who it was blocking
    assert rec.before_metrics["application_name"] == "blk_holder"
    assert rec.after_metrics["exists"] is False
    assert rec.after_metrics["blocked_waiters"] == 0           # the others are free
    assert sorted(done) == [0, 1]                              # both waiters committed on their own
    with psycopg.connect(settings.primary_dsn) as admin:      # agent_ro has no SELECT on user tables, by design
        v = admin.execute(f"SELECT v FROM {TABLE} WHERE id = 1").fetchone()[0]
    assert v == 2                                              # holder's write rolled back; waiters' kept

def test_idle_session_that_blocks_nobody_is_spared_when_blocking_required(ro, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    monkeypatch.setattr(settings, "kill_requires_blocking", True)
    c = psycopg.connect(settings.primary_dsn, autocommit=False, application_name="blk_idle")
    try:
        c.execute("SELECT txid_current()")
        pid = c.info.backend_pid
        wait_for(lambda: idle_pid_state(ro, pid) == "idle in transaction", "idle in txn")
        f = Finding(type=FindingType.IDLE_IN_TXN, severity="warning", target=f"pid:{pid}", evidence={"pid": pid})
        a = ProposedAction(finding_id=f.id, action_id=ActionId.TERMINATE_BACKEND,
                           params={"pid": pid}, proposed_by="llm")
        d = evaluate(a, f, ro)
        assert d.verdict == Verdict.DENY
        assert {x.name for x in d.checks if not x.passed} == {"is_blocking_others"}
    finally:
        c.rollback(); c.close()
