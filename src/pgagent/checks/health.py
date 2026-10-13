from pgagent.config import settings as s
from pgagent.schemas import Finding, FindingType
from pgagent.tools.pg import get_table_stats, get_activity, get_replication

def check_tables(conn) -> list[Finding]:
    out = []
    for t in get_table_stats(conn):
        target = f"{t['schema']}.{t['table']}"
        total = t["live"] + t["dead"]
        if total and t["dead"] >= s.bloat_min_dead and t["dead"] / total >= s.bloat_dead_ratio:
            ratio = round(t["dead"] / total, 3)
            out.append(Finding(type=FindingType.TABLE_BLOAT,
                               severity="critical" if ratio >= 0.5 else "warning",
                               target=target,
                               evidence={"live": t["live"], "dead": t["dead"], "dead_ratio": ratio}))
        if (t["mod_since_analyze"] >= s.stale_min_mod
                and t["mod_since_analyze"] >= s.stale_mod_ratio * max(t["live"], 1)):
            out.append(Finding(type=FindingType.STALE_STATS, severity="warning", target=target,
                               evidence={"live": t["live"], "mod_since_analyze": t["mod_since_analyze"]}))
    return out

def check_sessions(conn) -> list[Finding]:
    out, rows = [], get_activity(conn)
    for r in rows:
        if r["state"] == "idle in transaction" and (r["state_age_s"] or 0) >= s.idle_txn_seconds:
            out.append(Finding(type=FindingType.IDLE_IN_TXN, severity="warning", target=f"pid:{r['pid']}",
                               evidence={"pid": r["pid"], "app": r["application_name"], "user": r["usename"],
                                         "idle_s": round(r["state_age_s"], 1), "xact_age_s": round(r["xact_age_s"] or 0, 1)}))
    # lock waits: one finding per root blocker
    waiting = [r for r in rows if r["wait_event_type"] == "Lock" and (r["query_age_s"] or 0) >= s.lock_wait_seconds]
    blockers: dict[int, list[dict]] = {}
    for w in waiting:
        for b in w["blocked_by"]:
            blockers.setdefault(b, []).append(w)
    for pid, ws in blockers.items():
        out.append(Finding(type=FindingType.LOCK_WAIT, severity="critical" if len(ws) >= 3 else "warning",
                           target=f"pid:{pid}",
                           evidence={"blocker_pid": pid, "waiters": len(ws),
                                     "max_wait_s": round(max(w["query_age_s"] for w in ws), 1)}))
    return out

def check_replication(conn) -> list[Finding]:
    return [Finding(type=FindingType.REPLICATION_LAG, severity="critical",
                    target=f"replica:{r['application_name']}",
                    evidence={"lag_bytes": r["lag_bytes"], "state": r["state"], "replay_lag_s": r["replay_lag_s"]})
            for r in get_replication(conn) if (r["lag_bytes"] or 0) >= s.repl_lag_bytes]

def run_all_checks(conn) -> list[Finding]:
    return check_tables(conn) + check_sessions(conn) + check_replication(conn)
