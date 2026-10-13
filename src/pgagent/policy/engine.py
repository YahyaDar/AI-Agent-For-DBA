from pydantic import ValidationError
from pgagent.audit.store import count_executed_last_hour
from pgagent.config import settings
from pgagent.policy.catalog import CATALOG, PROTECTED_SCHEMAS, PidParams, TableParams
from pgagent.schemas import (ActionId, CheckResult, Finding, PolicyDecision,
                             ProposedAction, RiskTier, Verdict)

AUTONOMOUS_TIERS = {RiskTier.LOW, RiskTier.MEDIUM}   # HIGH is never autonomous

def _verdict(tier, checks) -> PolicyDecision:
    failed = [c for c in checks if not c.passed]
    if failed:
        return PolicyDecision(verdict=Verdict.DENY, tier=tier, checks=checks,
                              reasons=[f"{c.name}: {c.detail}".rstrip(": ") for c in failed])
    return PolicyDecision(verdict=Verdict.ALLOW, tier=tier, checks=checks, reasons=["all checks passed"])

def evaluate(action: ProposedAction, finding: Finding, ro) -> PolicyDecision:
    """Deterministic gate. No LLM involved. Default deny."""
    checks: list[CheckResult] = []
    def chk(name, ok, detail=""):
        checks.append(CheckResult(name=name, passed=bool(ok), detail=detail))
        return ok

    spec = CATALOG.get(action.action_id)
    if spec is None:
        return PolicyDecision(verdict=Verdict.DENY, tier=RiskTier.HIGH, checks=checks,
                              reasons=["action not in catalog (default deny)"])
    tier = spec.tier

    try:
        p = spec.params(**action.params)
    except ValidationError as e:
        chk("params_valid", False, e.errors()[0]["msg"])
        return _verdict(tier, checks)
    chk("params_valid", True)

    if tier not in AUTONOMOUS_TIERS:
        return PolicyDecision(verdict=Verdict.ESCALATE, tier=tier, checks=checks,
                              reasons=[f"{tier.value} risk: human approval required"])

    chk("finding_justifies_action",
        finding.type == spec.finding_type and finding.id == action.finding_id,
        f"{action.action_id.value} requires finding type {spec.finding_type.value}")
    n = count_executed_last_hour(ro)
    chk("rate_limit", n < settings.max_actions_per_hour, f"{n}/{settings.max_actions_per_hour} actions in last hour")

    if isinstance(p, TableParams):
        target = f"{p.schema_name}.{p.table}"
        chk("target_matches_finding", target == finding.target, f"{target} vs {finding.target}")
        chk("schema_allowed", p.schema_name not in PROTECTED_SCHEMAS, p.schema_name)
        row = ro.execute(
            "SELECT c.relkind, pg_total_relation_size(c.oid)::bigint AS bytes FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname=%s AND c.relname=%s",
            (p.schema_name, p.table)).fetchone()
        chk("is_ordinary_table", row is not None and row["relkind"] == "r", "table missing or not a plain table")
        if action.action_id == ActionId.REINDEX_CONCURRENTLY and row:
            chk("size_limit", row["bytes"] <= settings.reindex_max_bytes, f"{row['bytes']} bytes")
    elif isinstance(p, PidParams):
        chk("target_matches_finding", finding.target == f"pid:{p.pid}", f"pid:{p.pid} vs {finding.target}")
        row = ro.execute(
            "SELECT backend_type, state, application_name, "
            "EXTRACT(EPOCH FROM now() - state_change)::float8 AS idle_s "
            "FROM pg_stat_activity WHERE pid=%s", (p.pid,)).fetchone()
        chk("backend_exists", row is not None, "pid no longer exists")
        if row:   # re-check LIVE state at decision time, not just the stale finding
            chk("is_client_backend", row["backend_type"] == "client backend", row["backend_type"])
            chk("still_idle_in_txn", row["state"] == "idle in transaction", str(row["state"]))
            chk("idle_long_enough", (row["idle_s"] or 0) >= settings.idle_txn_seconds, f"{row['idle_s']}s")
            chk("not_agent_session", not (row["application_name"] or "").startswith("pgagent"), "")
            if settings.kill_requires_blocking:
                n = ro.execute("SELECT count(*) AS n FROM pg_stat_activity "
                               "WHERE %s::int = ANY(pg_blocking_pids(pid))", (p.pid,)).fetchone()["n"]
                chk("is_blocking_others", n > 0, "session is not blocking anyone")
    return _verdict(tier, checks)
