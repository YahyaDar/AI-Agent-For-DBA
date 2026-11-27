# pgAgent: Autonomous & Copilot DBA for PostgreSQL

**pgAgent** (`ai-agent-for-dba`) is a deterministic, safety-first PostgreSQL health monitoring, autonomous healing, and on-demand AI DBA copilot system. 

Designed around strict operational guardrails, `pgAgent` separates **deterministic automated remediation** from **LLM-assisted root-cause analysis**, ensuring predictable, auditable actions on production-like PostgreSQL topologies.

---

## Key Architectural Principles

* **Deterministic Policy Engine ([ADR-0001](docs/adr/0001-deterministic-policy-engine.md)):** Routine remediation decisions are driven by a strictly typed, rule-based policy engine (`pgagent.policy`) rather than non-deterministic LLM outputs.
* **Dual-Database Isolation ([ADR-0002](docs/adr/0002-dual-database-target-store-split.md)):** Operational state, audit logs, and policy execution history are isolated in a dedicated audit store (`pgagent.audit`), separate from the monitored target PostgreSQL cluster.
* **Immediate Escalation & On-Demand Copilot ([ADR-0003](docs/adr/0003-immediate-escalation-on-demand-copilot.md)):** High-risk anomalies or blocked policy preconditions immediately escalate to human operators, paired with an on-demand RAG-backed copilot (`pgagent.copilot` & `pgagent.rag`) for deep diagnostics.
* **Least-Privilege Remediation ([ADR-0004](docs/adr/0004-security-definer-autovacuum-tuning.md)):** Privileged operations like per-table `autovacuum` tuning are encapsulated behind audited `SECURITY DEFINER` database functions rather than granting superuser roles to the agent.
* **Precondition Safety Gates ([ADR-0005](docs/adr/0005-policy-preconditions-escalate-on-gate-block.md)):** Every automated action passes through strict safety gates (e.g., replication lag checks, lock/blocking thresholds, wraparound margins). If a gate blocks execution, the agent halts and escalates.

---

## Repository Structure

```text
├── docker/                 # Multi-node PostgreSQL lab (Primary + Streaming Replica)
├── docs/
│   ├── adr/                # Architecture Decision Records (0001–0005)
│   ├── agents/             # Domain context, triage labels, and issue-tracker workflows
│   └── architecture.md     # System design & component dataflow
├── scenarios/              # Reproducible failure & chaos injection scenarios
│   ├── bloat.py            # Table bloat simulation
│   ├── idle_in_txn.py      # Long-running idle-in-transaction blockers
│   ├── index_bloat.py      # Index bloat & fragmentation
│   ├── replication_lag.py  # Streaming replication delay simulation
│   ├── stale_stats.py      # Outdated planner statistics
│   └── xid_wraparound.py   # Transaction ID (XID) wraparound pressure
├── scripts/                # Lab provisioning, calibration, and Phase 1 verification
├── src/pgagent/
│   ├── api/                # Service API layer
│   ├── audit/              # Immutable audit trail & state store
│   ├── checks/             # PostgreSQL catalog & health probes
│   ├── copilot/            # On-demand AI DBA diagnostic assistant
│   ├── executor/           # Guarded remediation action runners
│   ├── policy/             # Deterministic policy catalog & evaluation engine
│   ├── rag/                # Runbook & PostgreSQL documentation retrieval
│   ├── tools/              # Low-level DB connection & PG inspection utilities
│   ├── cli.py              # Command-line interface entrypoint
│   └── healer.py           # Main observe-decide-act-audit control loop
├── tests/                  # Unit, integration, guard, and adversarial policy test suites
└── workload/               # Continuous traffic generators (steady, churn, chaos)
```

---

## Quickstart & Local Lab

### Prerequisites
* Python `>= 3.11` (managed via [`uv`](https://docs.astral.sh/uv/))
* Docker & Docker Compose

### 1. Environment & Dependencies
```bash
cp .env.example .env
uv sync
```

### 2. Spin Up the PostgreSQL Primary/Replica Lab
```bash
docker compose up -d --build
# Or rebuild and initialize schemas/roles from scratch:
bash scripts/lab_rebuild.sh
```

### 3. Run Workloads & Fault Scenarios
Simulate realistic database traffic and inject specific PostgreSQL failure modes:
```bash
# Start background workload (steady + churn)
bash workload/run.sh

# Trigger a specific diagnostic/healing scenario
uv run python -m scenarios.run bloat
uv run python -m scenarios.run idle_in_txn
uv run python -m scenarios.run replication_lag
```

### 4. Run the Healer & Verification Suite
```bash
# Execute the pgAgent CLI
uv run pgagent --help

# Run threshold calibration and Phase 1 end-to-end verification
bash scripts/run_calibration.sh
bash scripts/verify_phase1.sh
```

---

## Testing

The test suite covers policy evaluation, safety gate enforcement, adversarial edge cases, and end-to-end healer integration:

```bash
uv run pytest
```

Key test suites include:
* `tests/test_policy_guards.py` & `tests/test_policy_adversarial.py` — Validates precondition gates and resistance to unsafe remediation triggers.
* `tests/test_blocking_kill.py` — Tests safe termination of blocking `idle in transaction` sessions.
* `tests/test_wraparound_and_index_policies.py` — Verifies XID wraparound and index bloat remediation boundaries.
* `tests/test_healer_integration.py` — End-to-end loop verification against the containerized database.

---

## Documentation

* [System Architecture](docs/architecture.md)
* [Domain Glossary](GLOSSARY.md)
* [Agent Guidelines & Triage](AGENTS.md)
* [Architecture Decision Records (ADRs)](docs/adr/)