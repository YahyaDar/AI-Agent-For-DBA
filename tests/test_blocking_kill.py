"""Blocking-aware kill. Needs the Docker lab running (skipped otherwise)."""

import threading

import psycopg
import pytest

from conftest import LAB_OWNER_DSN, LAB_PRIMARY_DSN, wait_for
from pgagent import healer
from pgagent.audit import store
from pgagent.config import settings
from pgagent.policy import evaluate
from pgagent.schemas import ActionId, Finding, FindingType, ProposedAction, Verdict
from pgagent.tools.db import ro_conn

pytestmark = pytest.mark.lab

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
    c = psycopg.connect(LAB_PRIMARY_DSN, autocommit=True)
    c.execute(f"DROP TABLE IF EXISTS {TABLE}")
    c.execute(f"CREATE TABLE {TABLE} (id int PRIMARY KEY, v int)")
    c.execute(f"GRANT ALL ON {TABLE} TO bluebox_owner")
    c.execute(f"INSERT INTO {TABLE} VALUES (1, 0), (2, 0)")
    yield
    c.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE application_name LIKE 'blk_%' AND pid <> pg_backend_pid()"
    )
    wait_for(
        lambda: c.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE application_name LIKE 'blk_%' AND pid <> pg_backend_pid()"
        ).fetchone()[0] == 0,
        "all blk_* sessions to terminate",
    )
    c.execute(f"DROP TABLE IF EXISTS {TABLE}")
    c.close()


def idle_pid_state(ro, pid):
    r = ro.execute("SELECT state FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone()
    return r and r["state"]


def make_blocker(ro, n_waiters):
    client_dsn = LAB_OWNER_DSN
    holder = psycopg.connect(client_dsn, autocommit=False, application_name="blk_holder")
    holder.execute(f"UPDATE {TABLE} SET v = v + 1 WHERE id = 1")  # row lock held, txn left open
    pid = holder.info.backend_pid
    wait_for(lambda: idle_pid_state(ro, pid) == "idle in transaction", "holder idle in txn")
    done = []

    def waiter(i):
        c = psycopg.connect(client_dsn, autocommit=False, application_name=f"blk_waiter_{i}")
        try:
            c.execute(f"UPDATE {TABLE} SET v = v + 1 WHERE id = 1")  # blocks until holder is gone
            c.commit()
            done.append(i)
        except Exception as e:  # noqa: BLE001
            done.append(f"err: {e}")
        finally:
            c.close()

    threads = [threading.Thread(target=waiter, args=(i,), daemon=True) for i in range(n_waiters)]
    for t in threads:
        t.start()
    wait_for(
        lambda: ro.execute(
            "SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type='Lock' "
            "AND application_name LIKE 'blk_waiter%'"
        ).fetchone()["n"]
        == n_waiters,
        "waiters blocked",
    )
    return holder, pid, threads, done


def test_blocking_idle_session_is_killed_and_waiters_proceed(ro, table, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    monkeypatch.setattr(settings, "kill_requires_blocking", True)
    records = []
    monkeypatch.setattr(store, "record", records.append)  # keep the real audit log clean

    _holder, pid, threads, done = make_blocker(ro, 2)
    f = Finding(type=FindingType.IDLE_IN_TXN, severity="critical", target=f"pid:{pid}", evidence={"pid": pid})
    healer.handle(f, ro)
    for t in threads:
        t.join(timeout=15)

    rec = records[0]
    assert rec.decision.verdict == Verdict.ALLOW and rec.result.status == "success"
    assert rec.before_metrics["blocked_waiters"] == 2  # victim record: who it was blocking
    assert rec.before_metrics["application_name"] == "blk_holder"
    assert rec.after_metrics["exists"] is False
    assert rec.after_metrics["blocked_waiters"] == 0  # the others are free
    assert sorted(done) == [0, 1]  # both waiters committed on their own
    with psycopg.connect(LAB_PRIMARY_DSN) as admin:  # agent_ro has no SELECT on user tables, by design
        v = admin.execute(f"SELECT v FROM {TABLE} WHERE id = 1").fetchone()[0]
    assert v == 2  # holder's write rolled back; waiters' kept


def test_transitive_lock_chain_records_direct_and_transitive_waiters(ro, table, monkeypatch):
    """3-hop transitive lock chain: H <- W1 <- W2.

    H (blk_holder) updates row 1 and stays idle in txn (holds lock on row 1).
    W1 (blk_waiter_1) updates row 2 (holds lock on row 2), then tries to update row 1 (blocked by H).
    W2 (blk_waiter_2) tries to update row 2 (blocked by W1).

    Victim record on H must show:
      direct_waiters == 1  (only W1 directly blocked by H)
      blocked_waiters == 2 (W1 direct + W2 transitive via recursive CTE)

    When H is killed:
      H rolls back. W1 unblocks, updates row 1, and commits (releasing row 2).
      W2 unblocks, updates row 2, and commits.
      Row 1 final value: 1 (W1).
      Row 2 final value: 2 (W1 and W2).
      Completion order is verified as ["w1", "w2"].
    """
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    monkeypatch.setattr(settings, "kill_requires_blocking", True)
    records = []
    monkeypatch.setattr(store, "record", records.append)

    # 1. Start holder
    holder = psycopg.connect(LAB_OWNER_DSN, autocommit=False, application_name="blk_holder")
    holder.execute(f"UPDATE {TABLE} SET v = v + 1 WHERE id = 1")
    h_pid = holder.info.backend_pid
    wait_for(lambda: idle_pid_state(ro, h_pid) == "idle in transaction", "holder idle in txn")

    c1 = psycopg.connect(LAB_OWNER_DSN, autocommit=False, application_name="blk_waiter_1")
    w1_pid = c1.info.backend_pid

    c2 = psycopg.connect(LAB_OWNER_DSN, autocommit=False, application_name="blk_waiter_2")
    w2_pid = c2.info.backend_pid

    completion_order = []
    w1_acquired_row2 = threading.Event()

    def run_w1():
        try:
            c1.execute(f"UPDATE {TABLE} SET v = v + 1 WHERE id = 2")  # holds lock on row 2
            w1_acquired_row2.set()
            c1.execute(f"UPDATE {TABLE} SET v = v + 1 WHERE id = 1")  # blocks on H
            c1.commit()
            completion_order.append("w1")
        finally:
            c1.close()

    def run_w2():
        try:
            c2.execute(f"UPDATE {TABLE} SET v = v + 1 WHERE id = 2")  # blocks on W1
            c2.commit()
            completion_order.append("w2")
        finally:
            c2.close()

    t1 = threading.Thread(target=run_w1, daemon=True)
    t1.start()
    w1_acquired_row2.wait(timeout=5)
    wait_for(
        lambda: ro.execute(
            "SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
            "AND application_name = 'blk_waiter_1'"
        ).fetchone()["n"] == 1,
        "waiter 1 blocked on row 1",
    )

    t2 = threading.Thread(target=run_w2, daemon=True)
    t2.start()
    wait_for(
        lambda: ro.execute(
            "SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
            "AND application_name = 'blk_waiter_2'"
        ).fetchone()["n"] == 1,
        "waiter 2 blocked on row 2",
    )

    # Verify live blocking graph before heal:
    # H blocks W1 directly; W1 blocks W2 directly
    f = Finding(type=FindingType.IDLE_IN_TXN, severity="critical", target=f"pid:{h_pid}", evidence={"pid": h_pid})
    healer.handle(f, ro)

    t1.join(timeout=15)
    t2.join(timeout=15)

    assert completion_order == ["w1", "w2"]

    rec = records[0]
    assert rec.decision.verdict == Verdict.ALLOW and rec.result.status == "success"
    # Proves recursive CTE distinguishes direct (1) from total transitive (2):
    assert rec.before_metrics["direct_waiters"] == 1
    assert rec.before_metrics["blocked_waiters"] == 2
    assert set(rec.before_metrics["waiter_pids"]) == {w1_pid, w2_pid}
    assert rec.after_metrics["exists"] is False
    assert rec.after_metrics["blocked_waiters"] == 0

    with psycopg.connect(LAB_PRIMARY_DSN) as admin:
        rows = dict(admin.execute(f"SELECT id, v FROM {TABLE} ORDER BY id").fetchall())
    # Row 1: H's v+1 rolled back; W1's v+1 committed => 1
    # Row 2: W1's v+1 committed; W2's v+1 committed => 2
    assert rows == {1: 1, 2: 2}


def test_idle_session_that_blocks_nobody_is_spared_when_blocking_required(ro, monkeypatch):
    monkeypatch.setattr(settings, "idle_txn_seconds", 0)
    monkeypatch.setattr(settings, "kill_requires_blocking", True)
    c = psycopg.connect(LAB_PRIMARY_DSN, autocommit=False, application_name="blk_idle")
    try:
        c.execute("SELECT txid_current()")
        pid = c.info.backend_pid
        wait_for(lambda: idle_pid_state(ro, pid) == "idle in transaction", "idle in txn")
        f = Finding(type=FindingType.IDLE_IN_TXN, severity="warning", target=f"pid:{pid}", evidence={"pid": pid})
        a = ProposedAction(
            finding_id=f.id,
            action_id=ActionId.TERMINATE_BACKEND,
            params={"pid": pid},
            proposed_by="llm",
        )
        d = evaluate(a, f, ro)
        assert d.verdict == Verdict.DENY
        assert {x.name for x in d.checks if not x.passed} == {"is_blocking_others"}
    finally:
        c.rollback()
        c.close()
