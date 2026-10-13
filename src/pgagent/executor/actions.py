import time
import psycopg
from psycopg import sql
from pglast import parse_sql
from pgagent.config import settings
from pgagent.policy.catalog import CATALOG, PidParams, TableParams
from pgagent.schemas import ActionId, ExecutionResult, ProposedAction

EXPECTED_STMT = {
    ActionId.VACUUM_TABLE: "VacuumStmt",
    ActionId.ANALYZE_TABLE: "VacuumStmt",          # ANALYZE parses as VacuumStmt too
    ActionId.REINDEX_CONCURRENTLY: "ReindexStmt",
    ActionId.TERMINATE_BACKEND: "SelectStmt",
}

def build_sql(action: ProposedAction, conn) -> str:
    """SQL comes only from fixed templates + validated params, never from the LLM."""
    p = CATALOG[action.action_id].params(**action.params)
    if isinstance(p, TableParams):
        ident = sql.Identifier(p.schema_name, p.table)
        tmpl = {ActionId.VACUUM_TABLE: "VACUUM {}",
                ActionId.ANALYZE_TABLE: "ANALYZE {}",
                ActionId.REINDEX_CONCURRENTLY: "REINDEX TABLE CONCURRENTLY {}"}[action.action_id]
        return sql.SQL(tmpl).format(ident).as_string(conn)
    assert isinstance(p, PidParams)
    return f"SELECT pg_terminate_backend({int(p.pid)})"

def validate_ast(sql_text: str, action_id: ActionId) -> None:
    """Parse with the real Postgres parser: exactly one statement, of exactly the expected kind."""
    stmts = parse_sql(sql_text)
    if len(stmts) != 1:
        raise ValueError(f"expected 1 statement, got {len(stmts)}")
    kind = type(stmts[0].stmt).__name__
    if kind != EXPECTED_STMT[action_id]:
        raise ValueError(f"statement type {kind} not allowed for {action_id.value}")

def snapshot(action: ProposedAction, ro) -> dict:
    p = CATALOG[action.action_id].params(**action.params)
    if isinstance(p, TableParams):
        row = ro.execute(
            "SELECT n_live_tup::bigint AS live, n_dead_tup::bigint AS dead, "
            "n_mod_since_analyze::bigint AS mod_since_analyze, last_vacuum, last_analyze "
            "FROM pg_stat_user_tables WHERE schemaname=%s AND relname=%s",
            (p.schema_name, p.table)).fetchone()
        return dict(row) if row else {}
    row = ro.execute("SELECT state, EXTRACT(EPOCH FROM now() - state_change)::float8 AS idle_s "
                     "FROM pg_stat_activity WHERE pid=%s", (p.pid,)).fetchone()
    return {"exists": row is not None, **(dict(row) if row else {})}

def execute(action: ProposedAction) -> ExecutionResult:
    # autocommit is required: VACUUM / REINDEX CONCURRENTLY cannot run inside a transaction block
    with psycopg.connect(settings.exec_dsn, autocommit=True, application_name="pgagent_exec",
                         options="-c lock_timeout=5000 -c statement_timeout=600000") as conn:
        sql_text = build_sql(action, conn)
        validate_ast(sql_text, action.action_id)
        if settings.dry_run:
            return ExecutionResult(status="skipped", sql_executed=sql_text)
        t0 = time.monotonic()
        try:
            cur = conn.execute(sql_text)
            if action.action_id == ActionId.TERMINATE_BACKEND and cur.fetchone()[0] is False:
                return ExecutionResult(status="failed", sql_executed=sql_text, error="pg_terminate_backend returned false")
            return ExecutionResult(status="success", sql_executed=sql_text,
                                   duration_ms=int((time.monotonic() - t0) * 1000))
        except Exception as e:
            return ExecutionResult(status="failed", sql_executed=sql_text, error=str(e))
