from __future__ import annotations
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field

def _now() -> datetime: return datetime.now(timezone.utc)

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")      # reject unknown fields from the LLM

class RiskTier(StrEnum):
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"

class FindingType(StrEnum):
    TABLE_BLOAT = "table_bloat"
    INDEX_BLOAT = "index_bloat"
    STALE_STATS = "stale_stats"
    LOCK_WAIT = "lock_wait"
    IDLE_IN_TXN = "idle_in_txn"
    REPLICATION_LAG = "replication_lag"
    DEADLOCK = "deadlock"

class ActionId(StrEnum):                            # the whitelist; grows in step 5
    ANALYZE_TABLE = "analyze_table"
    VACUUM_TABLE = "vacuum_table"
    REINDEX_CONCURRENTLY = "reindex_concurrently"
    TERMINATE_BACKEND = "terminate_backend"

class Finding(Strict):
    id: UUID = Field(default_factory=uuid4)
    type: FindingType
    severity: Literal["info", "warning", "critical"]
    target: str                                     # e.g. "public.pgbench_accounts" or "pid:1234"
    evidence: dict[str, Any]                        # raw numbers from the tool layer
    detected_at: datetime = Field(default_factory=_now)

class ProposedAction(Strict):
    finding_id: UUID
    action_id: ActionId                             # LLM can only pick from the enum
    params: dict[str, Any] = {}
    rationale: str = ""
    proposed_by: Literal["rules", "llm"]

class CheckResult(Strict):
    name: str
    passed: bool
    detail: str = ""

class Verdict(StrEnum):
    ALLOW = "allow"          # execute autonomously
    DENY = "deny"            # blocked
    ESCALATE = "escalate"    # route to advisory copilot / human

class PolicyDecision(Strict):
    verdict: Verdict
    tier: RiskTier
    reasons: list[str]
    checks: list[CheckResult] = []

class ExecutionResult(Strict):
    status: Literal["success", "failed", "skipped"]
    sql_executed: str | None = None
    duration_ms: int | None = None
    error: str | None = None

class AuditRecord(Strict):
    id: UUID = Field(default_factory=uuid4)
    ts: datetime = Field(default_factory=_now)
    finding: Finding
    action: ProposedAction | None
    decision: PolicyDecision
    before_metrics: dict[str, Any] = {}
    after_metrics: dict[str, Any] = {}
    result: ExecutionResult | None = None           # None if denied/escalated
    agent_trace: list[dict[str, Any]] = []          # LLM calls/reasoning, if any

class DiagnosisReport(Strict):
    question: str
    root_cause: str | None                          # None when falling back to raw evidence
    evidence: list[str]
    recommended_steps: list[str]
    confidence: float = Field(ge=0, le=1)
    samples_agreeing: int = 0
    samples_total: int = 0
    critic_passed: bool | None = None
    fallback_used: bool = False                     # True = "here's the evidence, no guess"
