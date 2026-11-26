# Project Roadmap: pgAgent (`ai-agent-for-dba`)

This roadmap outlines the phased evolution of **pgAgent** from a deterministic self-healing core into a full-featured, RAG-augmented PostgreSQL DBA Copilot and API platform.

---

## Phase 1: Deterministic Core & Lab Foundation *(Current / Completed)*
Focus: Establish safe, auditable, rule-based auto-remediation and a reproducible chaos-testing environment.

- [x] **Multi-Node Lab Topology:** Containerized PostgreSQL Primary + Streaming Replica setup (`docker/`, `compose.yaml`).
- [x] **Dual-Database Architecture ([ADR-0002](docs/adr/0002-dual-database-target-store-split.md)):** Strict separation between target workload DBs and the `pgagent` audit/state store (`src/pgagent/audit/`).
- [x] **Health Inspection & Catalog Checks (`src/pgagent/checks/`):** Telemetry collection for table/index bloat, stale statistics, blocking sessions, replication lag, and XID wraparound age.
- [x] **Deterministic Policy Engine ([ADR-0001](docs/adr/0001-deterministic-policy-engine.md)):** Rule catalog and evaluation engine (`src/pgagent/policy/`) with explicit safety precondition gates ([ADR-0005](docs/adr/0005-policy-preconditions-escalate-on-gate-block.md)).
- [x] **Least-Privilege Executor (`src/pgagent/executor/`):** Safe session termination and `SECURITY DEFINER` autovacuum tuning ([ADR-0004](docs/adr/0004-security-definer-autovacuum-tuning.md)).
- [x] **Chaos & Scenario Harness (`scenarios/`, `workload/`):** Reproducible fault injection and calibration scripts (`scripts/run_calibration.sh`, `scripts/verify_phase1.sh`).
- [x] **Adversarial & Guard Test Suite (`tests/`):** Comprehensive unit and integration coverage for policy safety boundaries.

---

## Phase 2: RAG Knowledge Base & On-Demand AI Copilot *(In Progress)*
Focus: Implement `src/pgagent/rag/` and `src/pgagent/copilot/` to assist DBAs when deterministic policies escalate ([ADR-0003](docs/adr/0003-immediate-escalation-on-demand-copilot.md)).

- [ ] **PG Runbook & ADR Ingestion (`src/pgagent/rag/`):**
  - Build ingestion pipeline for internal runbooks, ADRs, `GLOSSARY.md`, and PostgreSQL official documentation.
  - Implement hybrid vector + keyword retrieval backed by `pgvector` inside the audit/state database.
- [ ] **Context-Aware Diagnostic Copilot (`src/pgagent/copilot/`):**
  - Automatically assemble diagnostic context packets on policy escalation (recent health snapshots, blocked precondition gate details, active locks, and `pg_stat_statements` top offenders).
  - Generate grounded root-cause hypotheses and step-by-step manual remediation plans with citations.
- [ ] **Read-Only Interactive Investigation Tools:**
  - Provide bounded, read-only SQL inspection tools (`EXPLAIN` without `ANALYZE` on high-cost queries, catalog lookups) for interactive copilot sessions.
- [ ] **Copilot CLI Integration (`src/pgagent/cli.py`):**
  - Add `pgagent copilot explain <incident_id>` and interactive triage commands.

---

## Phase 3: Service API, Human-in-the-Loop Approvals & Alerting
Focus: Transition from CLI-driven execution to an always-on service with external operator workflows.

- [ ] **REST / Streaming API (`src/pgagent/api/`):**
  - Expose endpoints for health status, audit log inspection, manual remediation triggers, and copilot Q&A.
- [ ] **Human-in-the-Loop (HITL) Approval Workflow:**
  - When a precondition gate blocks an automated action (e.g., high replication lag or heavy lock contention), allow operators to review the copilot's risk assessment and explicitly approve/override via API or webhook.
- [ ] **Alerting & Issue Tracker Integration (`docs/agents/issue-tracker.md`):**
  - Webhook emitters for Slack, PagerDuty, and automated GitHub/Jira incident ticket creation using standardized triage labels (`docs/agents/triage-labels.md`).

---

## Phase 4: Expanded Remediation Playbooks & Multi-Cluster Support
Focus: Broaden PostgreSQL operational coverage and scale across multiple database clusters.

- [ ] **Expanded Policy Catalog:**
  - Automated `REINDEX CONCURRENTLY` workflows for severe index bloat under low-load windows.
  - Connection pool saturation & runaway query detection (`pg_stat_activity` + `pg_stat_statements` regression alerts).
  - Replication slot retention guardrails to prevent primary WAL disk exhaustion.
- [ ] **Multi-Cluster Fleet Management:**
  - Support monitoring multiple Primary/Replica clusters from a centralized `pgagent` control plane.
- [ ] **Observability & Metrics Export:**
  - Native Prometheus `/metrics` endpoint exposing policy evaluations, gate blocks, remediation counts, and copilot latency.