#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EVIDENCE_FILE="$REPO_DIR/reports/phase1/evidence/stage7_calibration.txt"

mkdir -p "$(dirname "$EVIDENCE_FILE")"

log() {
  echo "$@" | tee -a "$EVIDENCE_FILE"
}

log "=========================================================="
log "       PHASE 1 CALIBRATION RUN (READ-ONLY / DRY-RUN)     "
log "Start time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
log "=========================================================="
log ""

# Helper to capture user tables stats
dump_stats() {
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "
SELECT schemaname, relname, n_live_tup, n_dead_tup, n_mod_since_analyze, last_vacuum, last_analyze
FROM pg_stat_user_tables
WHERE (schemaname = 'bluebox' AND relname IN ('customer', 'rental'))
   OR schemaname = 'workload'
ORDER BY schemaname, relname;
"
}

# Helper to dump steady table write deltas
dump_steady_deltas() {
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "
SELECT relname, n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, n_dead_tup
FROM pg_stat_user_tables
WHERE schemaname = 'bluebox' AND relname IN ('customer', 'rental')
ORDER BY relname;
"
}

# Helper to dump audit rows in appdb
dump_audit() {
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d appdb -c "
SELECT ts, finding_type, target, action_id, verdict, status,
       record->'decision'->'reasons' AS reasons
FROM agent.audit
ORDER BY ts DESC
LIMIT 10;
"
}

# ---------------------------------------------------------
# PROFILE 1: STEADY (10 MINUTES)
# ---------------------------------------------------------
log "=========================================================="
log ">>> PROFILE 1: STEADY (10 minutes, checks every 60s)"
log "=========================================================="
"$REPO_DIR/workload/run.sh" reset | tee -a "$EVIDENCE_FILE"
docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres < "$REPO_DIR/scripts/reset_audit.sql" >/dev/null

log ""
log "--- Steady Profile: Baseline Stats Before Run ---"
dump_steady_deltas | tee -a "$EVIDENCE_FILE"

"$REPO_DIR/workload/run.sh" start steady | tee -a "$EVIDENCE_FILE"

for i in {1..10}; do
  sleep 60
  ts=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
  log ""
  log "[T+${i}m $ts] check result:"
  uv run python -m pgagent.cli check 2>&1 | tee -a "$EVIDENCE_FILE"
done

log ""
log "--- Steady Profile: Stats After 10m (Deltas Proving Writes) ---"
dump_steady_deltas | tee -a "$EVIDENCE_FILE"

log ""
log "--- Steady Profile: Testing once --dry-run and Audit Impact ---"
uv run python -m pgagent.cli once --dry-run 2>&1 | tee -a "$EVIDENCE_FILE"
log "Audit rows in appdb after dry-run:"
dump_audit | tee -a "$EVIDENCE_FILE"

"$REPO_DIR/workload/run.sh" stop | tee -a "$EVIDENCE_FILE"

# ---------------------------------------------------------
# PROFILE 2: CHURN (20k rows, 10 MINUTES)
# ---------------------------------------------------------
log ""
log "=========================================================="
log ">>> PROFILE 2: CHURN on workload.churn_items (10 minutes, checks every 60s)"
log "=========================================================="
"$REPO_DIR/workload/run.sh" reset | tee -a "$EVIDENCE_FILE"
docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres < "$REPO_DIR/scripts/reset_audit.sql" >/dev/null

"$REPO_DIR/workload/run.sh" start churn | tee -a "$EVIDENCE_FILE"

for i in {1..10}; do
  sleep 60
  ts=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
  log ""
  log "[T+${i}m $ts] check result:"
  uv run python -m pgagent.cli check 2>&1 | tee -a "$EVIDENCE_FILE"
  log "Table stats for workload.churn_items:"
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "
SELECT relname, n_live_tup, n_dead_tup, n_mod_since_analyze, round(n_dead_tup::numeric / NULLIF(n_live_tup + n_dead_tup, 0), 4) AS dead_ratio
FROM pg_stat_user_tables WHERE relname = 'churn_items';
" | tee -a "$EVIDENCE_FILE"
done

log ""
log "--- Churn Profile: Testing once --dry-run and Audit Impact ---"
uv run python -m pgagent.cli once --dry-run 2>&1 | tee -a "$EVIDENCE_FILE"
log "Audit rows in appdb after dry-run:"
dump_audit | tee -a "$EVIDENCE_FILE"

log ""
log "--- Testing second once --dry-run immediately (Demonstrating 120s cooldown impact) ---"
uv run python -m pgagent.cli once --dry-run 2>&1 | tee -a "$EVIDENCE_FILE"

"$REPO_DIR/workload/run.sh" stop | tee -a "$EVIDENCE_FILE"

# ---------------------------------------------------------
# PROFILE 3: CHURN_BIG (1.42M rows, 10 MINUTES)
# ---------------------------------------------------------
log ""
log "=========================================================="
log ">>> PROFILE 3: CHURN on workload.churn_big (10 minutes, checks every 60s)"
log "=========================================================="
"$REPO_DIR/workload/run.sh" reset | tee -a "$EVIDENCE_FILE"
docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres < "$REPO_DIR/scripts/reset_audit.sql" >/dev/null

"$REPO_DIR/workload/run.sh" start churn_big | tee -a "$EVIDENCE_FILE"

for i in {1..10}; do
  sleep 60
  ts=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
  log ""
  log "[T+${i}m $ts] check result:"
  uv run python -m pgagent.cli check 2>&1 | tee -a "$EVIDENCE_FILE"
  log "Table stats for workload.churn_big:"
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -c "
SELECT relname, n_live_tup, n_dead_tup, n_mod_since_analyze, round(n_dead_tup::numeric / NULLIF(n_live_tup + n_dead_tup, 0), 4) AS dead_ratio
FROM pg_stat_user_tables WHERE relname = 'churn_big';
" | tee -a "$EVIDENCE_FILE"
done

log ""
log "--- Churn Big Profile: Testing once --dry-run and Audit Impact ---"
uv run python -m pgagent.cli once --dry-run 2>&1 | tee -a "$EVIDENCE_FILE"
log "Audit rows in appdb after dry-run:"
dump_audit | tee -a "$EVIDENCE_FILE"

"$REPO_DIR/workload/run.sh" stop | tee -a "$EVIDENCE_FILE"

# ---------------------------------------------------------
# PROFILE 4: CHAOS (10 MINUTES)
# ---------------------------------------------------------
log ""
log "=========================================================="
log ">>> PROFILE 4: CHAOS (10 minutes, checks every 60s)"
log "=========================================================="
"$REPO_DIR/workload/run.sh" reset | tee -a "$EVIDENCE_FILE"
docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres < "$REPO_DIR/scripts/reset_audit.sql" >/dev/null

"$REPO_DIR/workload/run.sh" start chaos | tee -a "$EVIDENCE_FILE"

for i in {1..10}; do
  sleep 60
  ts=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
  log ""
  log "[T+${i}m $ts] check result:"
  uv run python -m pgagent.cli check 2>&1 | tee -a "$EVIDENCE_FILE"
done

log ""
log "--- Chaos Profile: Testing once --dry-run and Audit Impact ---"
uv run python -m pgagent.cli once --dry-run 2>&1 | tee -a "$EVIDENCE_FILE"
log "Audit rows in appdb after dry-run:"
dump_audit | tee -a "$EVIDENCE_FILE"

"$REPO_DIR/workload/run.sh" stop | tee -a "$EVIDENCE_FILE"

log ""
log "=========================================================="
log "Calibration complete: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
log "=========================================================="
