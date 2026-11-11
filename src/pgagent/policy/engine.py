from pydantic import ValidationError

from pgagent.audit.store import count_executed_last_hour
from pgagent.config import settings
from pgagent.policy.catalog import CATALOG, PROTECTED_SCHEMAS, PidParams, TableParams
from pgagent.schemas import (
    ActionId,
    CheckResult,
    Finding,
    PolicyDecision,
    ProposedAction,
    RiskTier,
    Verdict,
)

AUTONOMOUS_TIERS = {RiskTier.LOW, RiskTier.MEDIUM}   # HIGH is never autonomous
ESCALATE_CHECKS = {"vacuum_futility"}


def _action_set(*base: ActionId, include_freeze: bool = True) -> set[ActionId]:
    out = set(base)
    if include_freeze:
        freeze = getattr(ActionId, "VACUUM_FREEZE", None)
        if freeze is not None:
            out.add(freeze)
    return out


def _vacuum_actions() -> set[ActionId]:
    return _action_set(ActionId.VACUUM_TABLE)


def _heavy_maintenance_actions() -> set[ActionId]:
    return _action_set(ActionId.REINDEX_CONCURRENTLY)


def _vacuum_futility_blockers(ro) -> list[str]:
    if ro is None:
        return []
    blockers: list[str] = []
    txns = ro.execute(
        "SELECT pid, state, backend_xid::text AS backend_xid, backend_xmin::text AS backend_xmin, "
        "GREATEST(COALESCE(age(backend_xid), 0), COALESCE(age(backend_xmin), 0))::bigint AS xid_age "
        "FROM pg_stat_activity "
        "WHERE backend_type = 'client backend' "
        "AND datname = current_database() "
        "AND pid <> pg_backend_pid() "
        "AND COALESCE(application_name, '') NOT LIKE %s "
        "AND (backend_xid IS NOT NULL OR backend_xmin IS NOT NULL) "
        "AND GREATEST(COALESCE(age(backend_xid), 0), COALESCE(age(backend_xmin), 0)) > 0",
        ("pgagent%",),
    ).fetchall()
    for r in txns:
        xid = r.get("backend_xid") or r.get("backend_xmin")
        blockers.append(f"open transaction pid={r['pid']} ({r['state']}, xid={xid})")

    prep = ro.execute(
        "SELECT gid, transaction::text AS xid, age(transaction)::bigint AS xid_age "
        "FROM pg_prepared_xacts WHERE age(transaction) > 0"
    ).fetchall()
    for r in prep:
        blockers.append(f"prepared transaction gid={r['gid']} (xid={r['xid']})")

    slots = ro.execute(
        "SELECT slot_name, active, xmin::text AS xmin, catalog_xmin::text AS catalog_xmin "
        "FROM pg_replication_slots "
        "WHERE NOT active OR xmin IS NOT NULL OR catalog_xmin IS NOT NULL"
    ).fetchall()
    for r in slots:
        s_xmin = r.get("xmin") or r.get("catalog_xmin") or "none"
        blockers.append(f"replication slot {r['slot_name']} (active={r['active']}, xmin={s_xmin})")
    return blockers


def _verdict(tier, checks) -> PolicyDecision:
    failed = [c for c in checks if not c.passed]
    if failed:
        reasons = [f"{c.name}: {c.detail}".rstrip(": ") for c in failed]
        verdict = Verdict.ESCALATE if all(c.name in ESCALATE_CHECKS for c in failed) else Verdict.DENY
        return PolicyDecision(verdict=verdict, tier=tier, checks=checks, reasons=reasons)
    return PolicyDecision(verdict=Verdict.ALLOW, tier=tier, checks=checks, reasons=["all checks passed"])


def evaluate(action: ProposedAction, finding: Finding, ro, store=None) -> PolicyDecision:
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
    n = count_executed_last_hour(store)
    chk("rate_limit", n < settings.max_actions_per_hour, f"{n}/{settings.max_actions_per_hour} actions in last hour")
    action_limit = settings.per_action_max_per_hour.get(
        action.action_id.value, settings.max_actions_per_hour
    )
    n_action = count_executed_last_hour(store, action.action_id.value)
    chk(
        "action_rate_limit",
        n_action < action_limit,
        f"{n_action}/{action_limit} {action.action_id.value} in last hour",
    )

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
        if action.action_id in _heavy_maintenance_actions() and row and row["relkind"] == "r":
            conn_row = ro.execute(
                "SELECT (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend')::int AS active_conns, "
                "current_setting('max_connections')::int AS max_conns"
            ).fetchone()
            if conn_row and conn_row["max_conns"]:
                ratio = conn_row["active_conns"] / conn_row["max_conns"]
                chk(
                    "load_guard_connections",
                    ratio <= settings.load_guard_max_conn_ratio,
                    f"{conn_row['active_conns']}/{conn_row['max_conns']} connections "
                    f"({ratio:.2f} vs max {settings.load_guard_max_conn_ratio})",
                )
            lag_row = ro.execute(
                "SELECT COALESCE(max(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn))::bigint, 0) AS max_lag_bytes "
                "FROM pg_stat_replication"
            ).fetchone()
            max_lag = lag_row["max_lag_bytes"] if lag_row else 0
            chk(
                "load_guard_replication_lag",
                max_lag <= settings.load_guard_max_repl_lag_bytes,
                f"{max_lag}/{settings.load_guard_max_repl_lag_bytes} bytes replication lag",
            )
        if action.action_id in _vacuum_actions() and all(c.passed for c in checks):
            blockers = _vacuum_futility_blockers(ro)
            chk("vacuum_futility", not blockers, "; ".join(blockers))
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
