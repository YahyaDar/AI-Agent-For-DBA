import time
from pgagent.audit import store
from pgagent.checks import run_all_checks
from pgagent.config import settings
from pgagent.executor import execute, snapshot
from pgagent.policy import evaluate
from pgagent.schemas import (ActionId, AuditRecord, Finding, FindingType, PolicyDecision,
                             ProposedAction, RiskTier, Verdict)
from pgagent.tools.db import ro_conn

def propose(f: Finding) -> ProposedAction | None:
    """Rules-based proposer (the LLM proposer replaces this later). None = no autonomous fix exists."""
    if f.type in (FindingType.TABLE_BLOAT, FindingType.STALE_STATS):
        schema, table = f.target.split(".", 1)
        aid = ActionId.VACUUM_TABLE if f.type == FindingType.TABLE_BLOAT else ActionId.ANALYZE_TABLE
        return ProposedAction(finding_id=f.id, action_id=aid, proposed_by="rules",
                              params={"schema_name": schema, "table": table},
                              rationale=f"{f.type.value} detected")
    if f.type == FindingType.IDLE_IN_TXN:
        return ProposedAction(finding_id=f.id, action_id=ActionId.TERMINATE_BACKEND, proposed_by="rules",
                              params={"pid": f.evidence["pid"]}, rationale="long idle-in-transaction session")
    return None

def handle(f: Finding, ro) -> None:
    action = propose(f)
    if action is None:
        decision = PolicyDecision(verdict=Verdict.ESCALATE, tier=RiskTier.HIGH,
                                  reasons=["no autonomous action defined: route to advisory copilot / human"])
        store.record(AuditRecord(finding=f, action=None, decision=decision))
        print(f"[escalate] {f.type.value} {f.target} -> human/copilot")
        return
    decision = evaluate(action, f, ro)
    if decision.verdict != Verdict.ALLOW:
        store.record(AuditRecord(finding=f, action=action, decision=decision))
        print(f"[{decision.verdict.value}] {action.action_id.value} {f.target}: {'; '.join(decision.reasons)}")
        return
    before = snapshot(action, ro)
    result = execute(action)
    time.sleep(1)                       # let stats settle before the after-snapshot
    after = snapshot(action, ro)
    store.record(AuditRecord(finding=f, action=action, decision=decision,
                             before_metrics=before, after_metrics=after, result=result))
    print(f"[allow] {action.action_id.value} {f.target} -> {result.status}"
          f"{' (' + str(result.duration_ms) + 'ms)' if result.duration_ms is not None else ''}"
          f"{' ERROR: ' + result.error if result.error else ''}")

def run_cycle() -> None:
    with ro_conn() as ro:
        findings = run_all_checks(ro)
        print(f"[cycle] {len(findings)} finding(s)")
        for f in findings:
            if store.seen_recently(ro, f, settings.cooldown_s):
                continue
            try:
                handle(f, ro)
            except Exception as e:
                print(f"[error] {f.type.value} {f.target}: {e}")
