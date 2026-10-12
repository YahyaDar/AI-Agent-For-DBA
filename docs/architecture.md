# Module contracts

| Module | Signature | Owner |
|---|---|---|
| tools | `get_lock_graph(conn) -> dict`, etc. (typed returns) | A |
| checks | `run_all_checks(conn) -> list[Finding]` | A |
| policy | `evaluate(action: ProposedAction, finding: Finding) -> PolicyDecision` | A |
| executor | `execute(action, decision) -> ExecutionResult` | A |
| audit | `record(AuditRecord) -> None` | A |
| rag | `retrieve(query: str, k: int) -> list[Chunk]` | B |
| copilot | `diagnose(question: str) -> DiagnosisReport` | B |
| api | thin FastAPI over the above | B |

Rules: `main` is protected; work via short-lived branches and PRs;
changes to `schemas.py` need the other person's review.
