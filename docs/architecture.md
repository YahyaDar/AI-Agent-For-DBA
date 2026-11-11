# Architecture and Contracts

## Module contracts

| Module | Signature | Owner |
|---|---|---|
| tools | `get_lock_graph(conn) -> dict`, etc. (typed returns) | A |
| checks | `run_all_checks(conn) -> list[Finding]` | A |
| policy | `evaluate(action: ProposedAction, finding: Finding, ro, store=None) -> PolicyDecision` | A |
| executor | `execute(action: ProposedAction) -> ExecutionResult` | A |
| audit | `record(AuditRecord) -> None` | A |
| rag | `retrieve(query: str, k: int) -> list[Chunk]` | B |
| copilot | `diagnose(question: str) -> DiagnosisReport` | B |
| api | thin FastAPI over the above | B |

Rules: `main` is protected; work via short-lived branches and PRs; changes to `schemas.py` need the other person's review.

## Cluster Architecture: Target vs Store Split

The PostgreSQL 17 cluster is partitioned into two distinct databases to enforce least privilege and separation of concerns:

1. **Monitored Target (`bluebox`)**:
   - Contains the target application schema and workload (`bluebox` database).
   - Monitored by `agent_ro` and maintained by `agent_exec`.
   - Never contains agent audit state.

2. **Agent Store (`appdb`)**:
   - Contains the agent's immutable audit trail (`agent.audit` table with append-only trigger `audit_no_mod`).
   - Accessed exclusively via `agent_store` (for writes/reads) and `agent_ro` (for inspection).

## Role Architecture & Least Privilege

Superuser (`postgres`) is strictly forbidden in runtime code under `src/pgagent/` and is restricted to lab initialization (`docker/`, `scripts/`, `scenarios/`, test fixtures).

| Role | Database(s) | Grants / Privileges | Constraints | Purpose |
|---|---|---|---|---|
| `bluebox_owner` | `bluebox` | Database owner, owns all Bluebox tables, views, procedures | Non-superuser | Owns application objects |
| `agent_ro` | `bluebox`, `appdb` | `pg_monitor`, `CONNECT`, `default_transaction_read_only=on`, `statement_timeout=5s` | No SELECT on user tables, no DDL/DML | Read-only observation, detectors, policy re-checks |
| `agent_exec` | `bluebox` | `pg_maintain`, `pg_signal_backend`, `pg_read_all_stats`, `CONNECT`, `USAGE` on schemas | NOSUPERUSER, no SELECT on user tables, no DDL/DML, cannot terminate superusers | Autonomous execution (VACUUM, ANALYZE, REINDEX CONCURRENTLY, kill idle sessions) |
| `agent_store` | `appdb` | `CONNECT`, `USAGE` on schema `agent`, `INSERT`, `SELECT` on `agent.audit` | NOSUPERUSER, no UPDATE/DELETE/DROP (enforced by privileges + trigger), cannot connect to `bluebox` | Immutable audit logging and rate limiting |
