import psycopg
from pgagent.config import settings
from pgagent.schemas import AuditRecord, Finding

def record(rec: AuditRecord) -> None:
    with psycopg.connect(settings.exec_dsn, autocommit=True, application_name="pgagent_audit") as c:
        c.execute(
            "INSERT INTO agent.audit (id, ts, finding_type, target, action_id, verdict, tier, status, record) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (rec.id, rec.ts, rec.finding.type.value, rec.finding.target,
             rec.action.action_id.value if rec.action else None,
             rec.decision.verdict.value, rec.decision.tier.value,
             rec.result.status if rec.result else None, rec.model_dump_json()))

def seen_recently(ro, f: Finding, seconds: int) -> bool:
    return ro.execute(
        "SELECT 1 FROM agent.audit WHERE finding_type=%s AND target=%s "
        "AND ts > now() - make_interval(secs => %s) LIMIT 1",
        (f.type.value, f.target, seconds)).fetchone() is not None

def count_executed_last_hour(ro) -> int:
    return ro.execute("SELECT count(*) AS n FROM agent.audit "
                      "WHERE status='success' AND ts > now() - interval '1 hour'").fetchone()["n"]

def list_recent(ro, n: int = 20) -> list[dict]:
    return ro.execute(
        "SELECT ts, finding_type, target, action_id, verdict, tier, status, "
        "record->'decision'->'reasons' AS reasons FROM agent.audit ORDER BY ts DESC LIMIT %s", (n,)).fetchall()
