# pgagent

An autonomous and advisory database administration agent for PostgreSQL that pairs deterministic, low-to-medium-risk self-healing with human-in-the-loop diagnosis for high-risk incidents.

## Language

### Architecture & Authorities

**Monitored Target**:
The PostgreSQL database containing application schemas, user data, and client workloads observed and maintained by the agent (`bluebox`).
_Avoid_: App database, primary DB, client DB

**Agent Store**:
The isolated PostgreSQL database (`appdb`) that persists the agent's append-only audit trail, rate-limit history, and retrieved knowledge base.
_Avoid_: Metadata DB, logging DB, backend DB

**Healer**:
The scheduled autonomous control loop that runs detectors, proposes actions for low- and medium-risk findings, gates them through the Policy Engine, executes allowed actions, and records before/after snapshots in the Audit Log.
_Avoid_: Auto-fixer, cron worker, background daemon

**Advisory Copilot**:
The on-demand, read-only diagnostic engine invoked by a human DBA to investigate escalated or high-risk incidents against live database state, synthesize retrieved runbooks and incident memory, and recommend remediation steps without executing them.
_Avoid_: Chatbot, AI assistant, auto-escalator

**Policy Engine**:
The deterministic, code-only gate that evaluates a Proposed Action against a Finding and live database state to return a Policy Decision (`allow`, `deny`, or `escalate`). Never delegates safety judgements to an LLM.
_Avoid_: Guardrail prompt, safety filter, LLM judge

### Detection & Remediation

**Finding**:
A structured observation of a database anomaly emitted by a read-only detector at a point in time, identifying the anomaly type, severity, target resource, and raw evidence metrics.
_Avoid_: Alert, event, issue, ticket

**Proposed Action**:
A candidate remediation pairing a whitelisted Action ID and typed parameters with the specific Finding ID it addresses and its proposer (`rules` or `llm`).
_Avoid_: Fix command, SQL payload, remediation script

**Risk Tier**:
The static blast-radius classification assigned to each whitelisted action in the catalog (`low`, `medium`, or `high`). Only `low` and `medium` actions may execute autonomously; `high` always escalates.
_Avoid_: Severity, priority, confidence level

**Verdict**:
The outcome of a Policy Engine evaluation: `allow` (execute autonomously), `deny` (blocked due to failed precondition or safety check), or `escalate` (alert immediately and defer to the Advisory Copilot and human DBA).
_Avoid_: Status, result, approval

**Victim Record**:
The pre-execution snapshot captured immediately before terminating or cancelling a backend session, preserving the session's identity, transaction state, last query, held locks, and direct/transitive blocked waiters.
_Avoid_: Session log, kill dump, process trace

**Escalation Alert**:
The immediate notification emitted when the Healer records an `escalate` verdict, surfacing the finding ID, target resource, and evidence summary so a human DBA can invoke on-demand diagnosis.
_Avoid_: Pager alert, background diagnosis, error log

### Advisory Copilot & Knowledge

**Self-Consistency Voting**:
The diagnostic protocol that samples multiple independent root-cause hypotheses from the LLM over the same live telemetry and measures agreement before running a critic check.
_Avoid_: Majority vote, ensemble model, retry loop

**Incident Memory**:
Human-confirmed historical diagnosis and remediation records stored as embeddings in the Agent Store and retrieved strictly as untrusted data context during future investigations.
_Avoid_: Model fine-tuning, agent weights, chat history

### Throttling & Safety

**Cooldown**:
The per-target time window during which a recently evaluated `(FindingType, target)` pair is skipped by the Healer to prevent repeated handling and audit spam.
_Avoid_: Rate limit, debounce, sleep interval

**Rate Limit**:
The circuit-breaker cap on the number of successfully executed autonomous actions within a rolling one-hour window across the cluster.
_Avoid_: Cooldown, throttle, quota

**Vacuum Futility**:
A condition where running `VACUUM` cannot reclaim dead tuples or advance `relfrozenxid` because an older transaction horizon (`OldestXmin`) is pinned by an open transaction, prepared transaction, or replication slot.
_Avoid_: Vacuum failure, blocked vacuum
