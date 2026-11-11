"""Typed read-only diagnostic tools. Plain dict rows in, no side effects."""

import psycopg


def get_table_stats(conn) -> list[dict]:
    return conn.execute("""
        SELECT s.schemaname AS schema, s.relname AS "table",
               s.n_live_tup::bigint AS live, s.n_dead_tup::bigint AS dead,
               s.n_mod_since_analyze::bigint AS mod_since_analyze,
               age(c.relfrozenxid)::bigint AS xid_age
        FROM pg_stat_user_tables s
        JOIN pg_class c ON c.oid = s.relid
        WHERE s.schemaname <> 'agent'
    """).fetchall()

def get_index_stats(conn) -> list[dict]:
    return conn.execute("""
        SELECT n.nspname AS schema,
               t.relname AS "table",
               i.relname AS index_name,
               i.oid AS index_oid,
               pg_relation_size(i.oid)::bigint AS index_bytes,
               ix.indisvalid AS is_valid,
               am.amname
        FROM pg_index ix
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_am am ON am.oid = i.relam
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast', 'agent')
          AND t.relkind = 'r'
    """).fetchall()

def get_btree_index_stat(conn, index_oid: int) -> dict | None:
    try:
        row = conn.execute("SELECT * FROM pgstatindex(%s::regclass)", (index_oid,)).fetchone()
        return dict(row) if row else None
    except psycopg.Error:
        return None

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
