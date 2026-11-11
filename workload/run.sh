#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_DIR="$SCRIPT_DIR/pids"
mkdir -p "$PID_DIR"

ensure_tables() {
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox << 'EOF' >/dev/null
CREATE SCHEMA IF NOT EXISTS workload;
ALTER SCHEMA workload OWNER TO bluebox_owner;
GRANT USAGE, CREATE ON SCHEMA workload TO bluebox_owner;
GRANT USAGE ON SCHEMA workload TO agent_exec, agent_ro;

CREATE TABLE IF NOT EXISTS workload.churn_items (
    id int PRIMARY KEY,
    payload text,
    counter int,
    updated_at timestamptz
);
ALTER TABLE workload.churn_items OWNER TO bluebox_owner;
ALTER TABLE workload.churn_items SET (autovacuum_enabled = false);

INSERT INTO workload.churn_items (id, payload, counter, updated_at)
SELECT g, repeat('x', 200), 0, now()
FROM generate_series(1, 20000) g
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS workload.chaos_lock (
    id int PRIMARY KEY,
    v int
);
ALTER TABLE workload.chaos_lock OWNER TO bluebox_owner;
INSERT INTO workload.chaos_lock (id, v) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'workload' AND tablename = 'churn_big') THEN
    CREATE TABLE workload.churn_big AS SELECT * FROM bluebox.rental;
    ALTER TABLE workload.churn_big OWNER TO bluebox_owner;
    ALTER TABLE workload.churn_big SET (autovacuum_enabled = false);
    ALTER TABLE workload.churn_big ADD PRIMARY KEY (rental_id);
  END IF;
END $$;
EOF
  # Clean initial state outside transaction block
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "VACUUM ANALYZE workload.churn_items;" >/dev/null 2>&1 || true
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "VACUUM ANALYZE workload.churn_big;" >/dev/null 2>&1 || true
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "ANALYZE workload.chaos_lock;" >/dev/null 2>&1 || true
}

start_profile() {
  local profile="$1"
  ensure_tables

  if [ -f "$PID_DIR/${profile}.pid" ]; then
    local pid
    pid=$(cat "$PID_DIR/${profile}.pid")
    if kill -0 "$pid" 2>/dev/null; then
      echo "Profile '$profile' already running with PID $pid"
      return 0
    else
      rm -f "$PID_DIR/${profile}.pid"
    fi
  fi

  case "$profile" in
    steady)
      echo "Starting steady OLTP workload (application_name=workload_steady)..."
      nohup docker compose -f "$REPO_DIR/compose.yaml" exec -T \
        -e PGAPPNAME=workload_steady \
        -e PGPASSWORD=bluebox_owner_pw \
        primary pgbench -n -U bluebox_owner -d bluebox -c 4 -T 86400 -f - < "$SCRIPT_DIR/steady.sql" > "$PID_DIR/steady.log" 2>&1 &
      echo $! > "$PID_DIR/steady.pid"
      echo "Started steady (PID: $(cat "$PID_DIR/steady.pid"))"
      ;;
    churn)
      echo "Starting churn workload on workload.churn_items (application_name=workload_churn)..."
      nohup docker compose -f "$REPO_DIR/compose.yaml" exec -T \
        -e PGAPPNAME=workload_churn \
        -e PGPASSWORD=bluebox_owner_pw \
        primary pgbench -n -U bluebox_owner -d bluebox -c 4 -T 86400 -f - < "$SCRIPT_DIR/churn.sql" > "$PID_DIR/churn.log" 2>&1 &
      echo $! > "$PID_DIR/churn.pid"
      echo "Started churn (PID: $(cat "$PID_DIR/churn.pid"))"
      ;;
    churn_big)
      echo "Starting churn workload on workload.churn_big (application_name=workload_churn_big)..."
      nohup docker compose -f "$REPO_DIR/compose.yaml" exec -T \
        -e PGAPPNAME=workload_churn_big \
        -e PGPASSWORD=bluebox_owner_pw \
        primary pgbench -n -U bluebox_owner -d bluebox -c 4 -T 86400 -f - < "$SCRIPT_DIR/churn_big.sql" > "$PID_DIR/churn_big.log" 2>&1 &
      echo $! > "$PID_DIR/churn_big.pid"
      echo "Started churn_big (PID: $(cat "$PID_DIR/churn_big.pid"))"
      ;;
    chaos)
      echo "Starting chaos workload (application_name=workload_chaos)..."
      nohup uv run python "$SCRIPT_DIR/chaos.py" > "$PID_DIR/chaos.log" 2>&1 &
      echo $! > "$PID_DIR/chaos.pid"
      echo "Started chaos (PID: $(cat "$PID_DIR/chaos.pid"))"
      ;;
    *)
      echo "Unknown profile: $profile. Available profiles: steady, churn, churn_big, chaos"
      exit 1
      ;;
  esac
}

stop_all() {
  echo "Stopping all workload processes..."
  for pidfile in "$PID_DIR"/*.pid; do
    if [ -f "$pidfile" ]; then
      local pid
      pid=$(cat "$pidfile")
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        pkill -P "$pid" 2>/dev/null || true
      fi
      rm -f "$pidfile"
    fi
  done

  # Terminate any remaining workload database sessions
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox << 'EOF' >/dev/null 2>&1 || true
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE application_name LIKE 'workload_%' AND pid <> pg_backend_pid();
EOF
  echo "All workload processes stopped and sessions terminated."
}

show_status() {
  echo "=== Workload Processes ==="
  for p in steady churn churn_big chaos; do
    if [ -f "$PID_DIR/${p}.pid" ]; then
      local pid
      pid=$(cat "$PID_DIR/${p}.pid")
      if kill -0 "$pid" 2>/dev/null; then
        echo "Profile '$p': RUNNING (PID $pid)"
      else
        echo "Profile '$p': DEAD (Stale PID $pid)"
      fi
    else
      echo "Profile '$p': STOPPED"
    fi
  done

  echo ""
  echo "=== Active Workload Sessions in Database ==="
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "
SELECT pid, usename, application_name, state, wait_event_type, wait_event,
       round(EXTRACT(EPOCH FROM now() - state_change))::int AS state_age_s
FROM pg_stat_activity
WHERE application_name LIKE 'workload_%' AND pid <> pg_backend_pid();
"

  echo ""
  echo "=== Workload Table Metrics ==="
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "
SELECT schemaname, relname, n_live_tup, n_dead_tup, n_mod_since_analyze, last_vacuum, last_analyze
FROM pg_stat_user_tables
WHERE schemaname = 'workload'
ORDER BY relname;
"
}

reset_all() {
  echo "Resetting workloads and tables..."
  stop_all

  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox << 'EOF'
DROP SCHEMA IF EXISTS workload CASCADE;
CREATE SCHEMA workload;
ALTER SCHEMA workload OWNER TO bluebox_owner;
GRANT USAGE, CREATE ON SCHEMA workload TO bluebox_owner;
GRANT USAGE ON SCHEMA workload TO agent_exec, agent_ro;

CREATE TABLE workload.churn_items (
    id int PRIMARY KEY,
    payload text,
    counter int,
    updated_at timestamptz
);
ALTER TABLE workload.churn_items OWNER TO bluebox_owner;
ALTER TABLE workload.churn_items SET (autovacuum_enabled = false);

INSERT INTO workload.churn_items (id, payload, counter, updated_at)
SELECT g, repeat('x', 200), 0, now()
FROM generate_series(1, 20000) g;

CREATE TABLE workload.chaos_lock (
    id int PRIMARY KEY,
    v int
);
ALTER TABLE workload.chaos_lock OWNER TO bluebox_owner;
INSERT INTO workload.chaos_lock (id, v) VALUES (1, 0);

CREATE TABLE workload.churn_big AS SELECT * FROM bluebox.rental;
ALTER TABLE workload.churn_big OWNER TO bluebox_owner;
ALTER TABLE workload.churn_big SET (autovacuum_enabled = false);
ALTER TABLE workload.churn_big ADD PRIMARY KEY (rental_id);
EOF

  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "VACUUM ANALYZE workload.churn_items;"
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "VACUUM ANALYZE workload.churn_big;"
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "ANALYZE workload.chaos_lock;"
  echo "Reset complete."
}

cmd="${1:-status}"
case "$cmd" in
  start)
    if [ $# -lt 2 ]; then
      echo "Usage: $0 start <steady|churn|churn_big|chaos>"
      exit 1
    fi
    start_profile "$2"
    ;;
  stop)
    stop_all
    ;;
  status)
    show_status
    ;;
  reset)
    reset_all
    ;;
  *)
    echo "Usage: $0 {start <profile>|stop|status|reset}"
    exit 1
    ;;
esac
