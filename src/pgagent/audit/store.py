from contextlib import contextmanager

from pgagent.schemas import AuditRecord, Finding
from pgagent.tools.db import store_conn


@contextmanager
def _get_store(store=None):
    if store is not None:
        yield store
    else:
        with store_conn() as c:
            yield c


def record(rec: AuditRecord, store=None) -> None:
    with _get_store(store) as c:
        c.execute(
            "INSERT INTO agent.audit (id, ts, finding_type, target, action_id, verdict, tier, status, record) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (
                rec.id,
                rec.ts,
                rec.finding.type.value,
                rec.finding.target,
                rec.action.action_id.value if rec.action else None,
                rec.decision.verdict.value,
                rec.decision.tier.value,
                rec.result.status if rec.result else None,
                rec.model_dump_json(),
            ),
        )


def seen_recently(store, f: Finding, seconds: int) -> bool:
    with _get_store(store) as c:
        return (
            c.execute(
                "SELECT 1 FROM agent.audit WHERE finding_type=%s AND target=%s "
                "AND status IS DISTINCT FROM 'skipped' "
                "AND ts > now() - make_interval(secs => %s) LIMIT 1",
                (f.type.value, f.target, seconds),
            ).fetchone()
            is not None
        )


def count_executed_last_hour(store=None, action_id: str | None = None) -> int:
    with _get_store(store) as c:
        if action_id is not None:
            return c.execute(
                "SELECT count(*) AS n FROM agent.audit "
                "WHERE status='success' AND action_id=%s AND ts > now() - interval '1 hour'",
                (action_id,),
            ).fetchone()["n"]
        return c.execute(
            "SELECT count(*) AS n FROM agent.audit "
            "WHERE status='success' AND ts > now() - interval '1 hour'"
        ).fetchone()["n"]


def list_recent(store=None, n: int = 20) -> list[dict]:
    with _get_store(store) as c:
        return c.execute(
            "SELECT ts, finding_type, target, action_id, verdict, tier, status, "
            "record->'decision'->'reasons' AS reasons FROM agent.audit ORDER BY ts DESC LIMIT %s",
            (n,),
        ).fetchall()
