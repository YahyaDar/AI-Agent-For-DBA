from pgagent.config import settings as s
from pgagent.schemas import Finding, FindingType
from pgagent.tools.pg import (
    get_activity,
    get_btree_index_stat,
    get_index_stats,
    get_replication,
    get_table_stats,
)


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
        if t.get("xid_age") is not None and t["xid_age"] >= s.xid_age_warn:
            severity = "critical" if t["xid_age"] >= s.xid_age_emergency else "warning"
            out.append(Finding(
                type=FindingType.XID_WRAPAROUND,
                severity=severity,
                target=target,
                evidence={
                    "xid_age": t["xid_age"],
                    "warn_threshold": s.xid_age_warn,
                    "emergency_threshold": s.xid_age_emergency,
                },
            ))
    return out

def check_indexes(conn) -> list[Finding]:
    out = []
    table_indexes: dict[str, list[dict]] = {}
    for idx in get_index_stats(conn):
        target = f"{idx['schema']}.{idx['table']}"
        table_indexes.setdefault(target, []).append(idx)

    for target, indexes in table_indexes.items():
        invalid = [i["index_name"] for i in indexes if not i["is_valid"]]
        if invalid:
            out.append(Finding(
                type=FindingType.INDEX_BLOAT,
                severity="critical",
                target=target,
                evidence={"invalid_indexes": invalid, "reason": "invalid_index"},
            ))
            continue

        bloated = []
        max_ratio = 0.0
        for i in indexes:
            if i.get("amname") == "btree" and (i.get("index_bytes") or 0) >= s.index_bloat_min_bytes:
                st = get_btree_index_stat(conn, i["index_oid"])
                if st and st.get("avg_leaf_density") is not None:
                    bloat_ratio = round((100.0 - float(st["avg_leaf_density"])) / 100.0, 3)
                    if bloat_ratio >= s.index_bloat_ratio:
                        bloated.append({
                            "index_name": i["index_name"],
                            "bloat_ratio": bloat_ratio,
                            "index_bytes": i["index_bytes"],
                            "density": st["avg_leaf_density"],
                        })
                        max_ratio = max(max_ratio, bloat_ratio)
        if bloated:
            out.append(Finding(
                type=FindingType.INDEX_BLOAT,
                severity="critical" if max_ratio >= 0.5 else "warning",
                target=target,
                evidence={
                    "bloated_indexes": bloated,
                    "bloat_ratio": max_ratio,
                    "index_name": bloated[0]["index_name"],
                },
            ))
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
    return check_tables(conn) + check_indexes(conn) + check_sessions(conn) + check_replication(conn)
