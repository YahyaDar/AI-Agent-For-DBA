"""Typed read-only diagnostic tools. Plain dict rows in, no side effects."""

def get_table_stats(conn) -> list[dict]:
    return conn.execute("""
        SELECT schemaname AS schema, relname AS "table",
               n_live_tup::bigint AS live, n_dead_tup::bigint AS dead,
               n_mod_since_analyze::bigint AS mod_since_analyze
        FROM pg_stat_user_tables
        WHERE schemaname <> 'agent'
    """).fetchall()

def get_activity(conn) -> list[dict]:
    return conn.execute("""
        SELECT pid, usename, application_name, state, wait_event_type, wait_event,
               EXTRACT(EPOCH FROM now() - xact_start)::float8   AS xact_age_s,
               EXTRACT(EPOCH FROM now() - state_change)::float8 AS state_age_s,
               EXTRACT(EPOCH FROM now() - query_start)::float8  AS query_age_s,
               pg_blocking_pids(pid) AS blocked_by,
               left(query, 200) AS query
        FROM pg_stat_activity
        WHERE backend_type = 'client backend'
          AND datname = current_database()
          AND pid <> pg_backend_pid()
          AND application_name NOT LIKE 'pgagent%%'
    """).fetchall()

def get_replication(conn) -> list[dict]:
    return conn.execute("""
        SELECT application_name, state,
               pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint AS lag_bytes,
               EXTRACT(EPOCH FROM replay_lag)::float8 AS replay_lag_s
        FROM pg_stat_replication
    """).fetchall()
