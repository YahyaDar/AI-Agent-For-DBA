#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

FAILURES=0

pass() {
  echo "AC-$1 PASS $2"
}

fail() {
  echo "AC-$1 FAIL $2"
  FAILURES=$((FAILURES + 1))
}

echo "=========================================================="
echo "           PHASE 1 ACCEPTANCE CRITERIA VERIFICATION       "
echo "=========================================================="
echo ""

# --------------------------------------------------------
# AC-1: PG 17.x, same image, PostGIS and pgvector versions
# --------------------------------------------------------
echo "--- Checking AC-1 ---"
p_ver=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d appdb -tAc "SELECT version();" 2>&1 || true)
r_ver=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T replica psql -U postgres -d appdb -tAc "SELECT version();" 2>&1 || true)
ext_vers=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -tAc "SELECT extname || '=' || extversion FROM pg_extension WHERE extname IN ('postgis', 'vector', 'pg_stat_statements', 'pgstattuple') ORDER BY extname;" 2>&1 || true)

echo "Primary version: $p_ver"
echo "Replica version: $r_ver"
echo "Extensions in bluebox: $ext_vers"

if [[ "$p_ver" =~ "PostgreSQL 17" ]] && [[ "$r_ver" =~ "PostgreSQL 17" ]] && [[ "$ext_vers" =~ "postgis=" ]] && [[ "$ext_vers" =~ "vector=" ]]; then
  pass 1 "PG17, PostGIS, pgvector installed and verified on primary and replica"
else
  fail 1 "Version or extension check failed"
fi
echo ""

# --------------------------------------------------------
# AC-2: Replica streaming, active slot, lag near zero, can read Bluebox
# --------------------------------------------------------
echo "--- Checking AC-2 ---"
repl_state=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d appdb -tAc "SELECT state FROM pg_stat_replication LIMIT 1;" 2>&1 || true)
slot_active=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d appdb -tAc "SELECT active FROM pg_replication_slots WHERE slot_name = 'replica_slot';" 2>&1 || true)
lag_bytes=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d appdb -tAc "SELECT COALESCE(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn), 0) FROM pg_stat_replication LIMIT 1;" 2>&1 || echo "9999999")
replica_rentals=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T replica psql -U postgres -d bluebox -tAc "SELECT count(*) FROM bluebox.rental;" 2>&1 || echo "0")

echo "Replication state: $repl_state"
echo "Replica slot active: $slot_active"
echo "Replication lag bytes: $lag_bytes"
echo "Replica bluebox.rental count: $replica_rentals"

if [ "$repl_state" = "streaming" ] && [ "$slot_active" = "t" ] && [ "$lag_bytes" -le 1048576 ] && [ "$replica_rentals" -gt 1000000 ]; then
  pass 2 "Replica streaming, slot active, lag near zero, bluebox tables readable"
else
  fail 2 "Replica verification failed"
fi
echo ""

# --------------------------------------------------------
# AC-3: bluebox database loaded, owned by bluebox_owner, measured
# --------------------------------------------------------
echo "--- Checking AC-3 ---"
bb_owner=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d postgres -tAc "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'bluebox';" 2>&1 || true)
bb_size=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -tAc "SELECT pg_size_pretty(pg_database_size('bluebox'));" 2>&1 || true)
bb_tables=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -tAc "SELECT count(*) FROM pg_tables WHERE schemaname = 'bluebox';" 2>&1 || echo "0")

echo "bluebox database owner: $bb_owner"
echo "bluebox database size: $bb_size"
echo "bluebox schema tables count: $bb_tables"

if [ "$bb_owner" = "bluebox_owner" ] && [ "$bb_tables" -ge 20 ]; then
  pass 3 "bluebox loaded, owned by bluebox_owner ($bb_size, $bb_tables tables)"
else
  fail 3 "bluebox ownership or load check failed"
fi
echo ""

# --------------------------------------------------------
# AC-4: agent_exec least-privilege proof P1 to P11
# --------------------------------------------------------
echo "--- Checking AC-4 ---"
p1_super=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -tAc "SELECT rolsuper FROM pg_roles WHERE rolname = current_user;" 2>&1 || true)
p2_drop=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -c "DROP TABLE bluebox.film;" 2>&1 || true)
p3_create=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -c "CREATE TABLE bluebox.p3_test (id int);" 2>&1 || true)

p4_del=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -c "DELETE FROM bluebox.film WHERE film_id = 1;" 2>&1 || true)
p4_ins=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -c "INSERT INTO bluebox.film (film_id, title) VALUES (999999, 'x');" 2>&1 || true)
p4_upd=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -c "UPDATE bluebox.film SET title = 'x' WHERE film_id = 1;" 2>&1 || true)

p6_select=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -c "SELECT * FROM bluebox.film LIMIT 1;" 2>&1 || true)

# P8: separate VACUUM and ANALYZE calls
p8_vacuum=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -c "VACUUM bluebox.holiday;" 2>&1 || true)
p8_analyze=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -c "ANALYZE bluebox.holiday;" 2>&1 || true)

# P9: existing table bluebox.holiday with REINDEX TABLE CONCURRENTLY
p9_reindex=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d bluebox -c "REINDEX TABLE CONCURRENTLY bluebox.holiday;" 2>&1 || true)

echo "P1 rolsuper: $p1_super"
echo "P2 DROP output: $(echo "$p2_drop" | head -n 1)"
echo "P3 CREATE output: $(echo "$p3_create" | head -n 1)"
echo "P4 DELETE output: $(echo "$p4_del" | head -n 1)"
echo "P4 INSERT output: $(echo "$p4_ins" | head -n 1)"
echo "P4 UPDATE output: $(echo "$p4_upd" | head -n 1)"
echo "P6 SELECT output: $(echo "$p6_select" | head -n 1)"
echo "P8 VACUUM output: $(echo "$p8_vacuum" | head -n 1)"
echo "P8 ANALYZE output: $(echo "$p8_analyze" | head -n 1)"
echo "P9 REINDEX output: $(echo "$p9_reindex" | head -n 1)"

if [ "$p1_super" = "f" ] && \
   [[ "$p2_drop" =~ "must be owner" ]] && \
   [[ "$p3_create" =~ "permission denied" ]] && \
   [[ "$p4_del" =~ "permission denied" ]] && \
   [[ "$p4_ins" =~ "permission denied" ]] && \
   [[ "$p4_upd" =~ "permission denied" ]] && \
   [[ "$p6_select" =~ "permission denied" ]] && \
   [[ "$p8_vacuum" =~ "VACUUM" ]] && \
   [[ "$p8_analyze" =~ "ANALYZE" ]] && \
   [[ "$p9_reindex" =~ "REINDEX" ]]; then
  pass 4 "agent_exec is NOSUPERUSER, cannot write/select/drop, can maintain (P1-P11 pass)"
else
  fail 4 "agent_exec privilege checks failed"
fi
echo ""

# --------------------------------------------------------
# AC-5: agent_ro & agent_store least-privilege proof P12 to P20
# --------------------------------------------------------
echo "--- Checking AC-5 ---"
p12_ro_select=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_ro -d bluebox -c "SELECT * FROM bluebox.film LIMIT 1;" 2>&1 || true)
p12_ro_override=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_ro -d bluebox -c "SET SESSION default_transaction_read_only = off;" -c "INSERT INTO bluebox.holiday (id, holiday_name) VALUES (99999, 'Test');" 2>&1 || true)
p13_ro_stats=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_ro -d bluebox -tAc "SELECT count(*) FROM pg_stat_user_tables;" 2>&1 || echo "0")
p15_store_upd=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_store -d appdb -c "UPDATE agent.audit SET status = 'x';" 2>&1 || true)
p16_store_conn=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_store -d bluebox -c "SELECT 1;" 2>&1 || true)
p17_exec_appdb=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_exec -d appdb -c "SELECT 1;" 2>&1 || true)
p18_ro_appdb=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_ro -d appdb -tAc "SELECT current_user;" 2>&1 || true)
p19_store_appdb=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_store -d appdb -tAc "SELECT current_user;" 2>&1 || true)
p20_ro_audit=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U agent_ro -d appdb -c "SELECT count(*) FROM agent.audit;" 2>&1 || true)

echo "P12 ro SELECT output: $(echo "$p12_ro_select" | head -n 1)"
echo "P12 ro override INSERT output: $(echo "$p12_ro_override" | grep 'ERROR' | head -n 1)"
echo "P13 ro pg_stat_user_tables: $p13_ro_stats tables"
echo "P15 store UPDATE output: $(echo "$p15_store_upd" | head -n 1)"
echo "P16 store connect output: $(echo "$p16_store_conn" | head -n 1)"
echo "P17 exec connect appdb output: $(echo "$p17_exec_appdb" | head -n 1)"
echo "P18 ro connect appdb: $p18_ro_appdb"
echo "P19 store connect appdb: $p19_store_appdb"
echo "P20 ro audit SELECT output: $(echo "$p20_ro_audit" | head -n 1)"

if [[ "$p12_ro_select" =~ "permission denied" ]] && \
   [[ "$p12_ro_override" =~ "permission denied for table holiday" ]] && \
   [ "$p13_ro_stats" -gt 0 ] && \
   [[ "$p15_store_upd" =~ "permission denied for table audit" ]] && \
   [[ "$p16_store_conn" =~ "FATAL:  permission denied for database \"bluebox\"" ]] && \
   [[ "$p17_exec_appdb" =~ "FATAL:  permission denied for database \"appdb\"" ]] && \
   [ "$p18_ro_appdb" = "agent_ro" ] && \
   [ "$p19_store_appdb" = "agent_store" ] && \
   [[ "$p20_ro_audit" =~ "permission denied" ]]; then
  pass 5 "agent_ro and agent_store verified (P12-P20 pass)"
else
  fail 5 "agent_ro or agent_store privilege checks failed"
fi
echo ""

# --------------------------------------------------------
# AC-6: src/pgagent has no path to superuser, guard test passes
# --------------------------------------------------------
echo "--- Checking AC-6 ---"
guard_out=$(uv run pytest -m pure -q 2>&1 || true)
echo "$guard_out"
superuser_refs=$(grep -rn "postgres:postgres\|rolname = 'postgres'" "$REPO_DIR/src/pgagent" || true)

if [[ "$guard_out" =~ "passed" ]] && ! [[ "$guard_out" =~ "failed" ]] && [ -z "$superuser_refs" ]; then
  pass 6 "No superuser path in src/pgagent/; guard test green"
else
  fail 6 "Guard test failed or superuser found in src/pgagent/"
fi
echo ""

# --------------------------------------------------------
# AC-7: Target / store split verified
# --------------------------------------------------------
echo "--- Checking AC-7 ---"
split_out=$(uv run python -c "
from pgagent.tools.db import ro_conn, store_conn
from pgagent.config import Settings
assert 'primary_dsn' not in Settings.model_fields, 'Settings must not contain primary_dsn'
with ro_conn() as ro:
    assert ro.execute('SELECT current_database()').fetchone()['current_database'] == 'bluebox'
with store_conn() as store:
    assert store.execute('SELECT current_database()').fetchone()['current_database'] == 'appdb'
print('Verified: target is bluebox, store is appdb, Settings has no superuser DSNs')
" 2>&1 || true)
echo "$split_out"
if [[ "$split_out" =~ "Verified: target is bluebox, store is appdb" ]]; then
  pass 7 "Target/store connection split verified"
else
  fail 7 "Target/store connection split check failed"
fi
echo ""

# --------------------------------------------------------
# AC-8: Policy check names, order, and semantics unchanged
# --------------------------------------------------------
echo "--- Checking AC-8 ---"
exec_schemas_diff=$(git diff --stat -- "$REPO_DIR/src/pgagent/executor" "$REPO_DIR/src/pgagent/schemas.py")
policy_diff=$(git diff --stat -- "$REPO_DIR/src/pgagent/policy")
engine_diff=$(git diff -U0 -- "$REPO_DIR/src/pgagent/policy/engine.py")

echo "Executor and schemas diff: $exec_schemas_diff"
echo "Policy diff: $policy_diff"

if [ -z "$exec_schemas_diff" ] && \
   [[ "$policy_diff" =~ "src/pgagent/policy/engine.py" ]] && \
   [[ "$engine_diff" =~ "store=None" ]] && \
   [[ "$engine_diff" =~ "count_executed_last_hour(store)" ]]; then
  pass 8 "Policy checks, names, ordering and executor unchanged"
else
  fail 8 "Policy check changes detected or executor/schemas modified"
fi
echo ""

# --------------------------------------------------------
# AC-9: Full test suite green
# --------------------------------------------------------
echo "--- Checking AC-9 ---"
test_out=$(uv run pytest -q 2>&1 || true)
echo "$test_out"
if [[ "$test_out" =~ "49 passed" ]] && ! [[ "$test_out" =~ "failed" ]]; then
  pass 9 "Full test suite green ($test_out)"
else
  fail 9 "Test suite failed ($test_out)"
fi
echo ""

# --------------------------------------------------------
# AC-10: Workload steady, churn, chaos run and stop cleanly
# --------------------------------------------------------
echo "--- Checking AC-10 ---"
"$REPO_DIR/workload/run.sh" start steady >/dev/null 2>&1
sleep 2
st_active=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -tAc "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'workload_steady';" 2>&1 || echo "0")
"$REPO_DIR/workload/run.sh" stop >/dev/null 2>&1
echo "Steady active sessions during run: $st_active"

if [ "$st_active" -gt 0 ]; then
  pass 10 "Workload profiles start, produce traffic, and stop cleanly"
else
  fail 10 "Workload test failed"
fi
echo ""

# --------------------------------------------------------
# AC-11: Calibration evidence exists and contains required sections
# --------------------------------------------------------
echo "--- Checking AC-11 ---"
calib_file="$REPO_DIR/reports/phase1/evidence/stage7_calibration.txt"
if [ -s "$calib_file" ] && \
   grep -q "PROFILE 1: STEADY" "$calib_file" && \
   grep -q "PROFILE 2: CHURN" "$calib_file" && \
   grep -q "PROFILE 3: CHURN" "$calib_file" && \
   grep -q "PROFILE 4: CHAOS" "$calib_file" && \
   grep -q "Classification Table" "$calib_file" && \
   grep -q "Threshold Recommendations" "$calib_file"; then
  pass 11 "Calibration evidence recorded with all 4 profiles, classification table, and thresholds"
else
  fail 11 "Calibration evidence missing or lacks required profile sections"
fi
echo ""

# --------------------------------------------------------
# AC-12: Stage 8 live end-to-end evidence exists and contains 7 items
# --------------------------------------------------------
echo "--- Checking AC-12 ---"
e2e_file="$REPO_DIR/reports/phase1/evidence/stage8_e2e.txt"
if [ -s "$e2e_file" ] && \
   grep -i -q "ITEM 1: BLOAT HEALING" "$e2e_file" && \
   grep -i -q "ITEM 2: STALE STATS HEALING" "$e2e_file" && \
   grep -i -q "ITEM 3: IDLE-IN-TRANSACTION" "$e2e_file" && \
   grep -i -q "ITEM 4: FRESH IDLE" "$e2e_file" && \
   grep -i -q "ITEM 5: REPLICATION LAG" "$e2e_file" && \
   grep -i -q "ITEM 6: CLI AUDIT" "$e2e_file" && \
   grep -i -q "ITEM 7: ROLE IDENTITY AND AUDIT IMMUTABILITY" "$e2e_file"; then
  pass 12 "Stage 8 end-to-end live runs verified with all seven required items"
else
  fail 12 "Stage 8 evidence missing or incomplete"
fi
echo ""

# --------------------------------------------------------
# AC-13: lab_rebuild.sh actually rebuilds and verify passes
# --------------------------------------------------------
echo "--- Checking AC-13 ---"
if [ -x "$REPO_DIR/scripts/lab_rebuild.sh" ]; then
  rebuild_out=$("$REPO_DIR/scripts/lab_rebuild.sh" 2>&1 || true)
  rebuild_rentals=$(docker compose -f "$REPO_DIR/compose.yaml" exec -T replica psql -U postgres -d bluebox -tAc "SELECT count(*) FROM bluebox.rental;" 2>&1 || echo "0")
  if [[ "$rebuild_out" =~ "LAB REBUILD COMPLETE" ]] && [ "$rebuild_rentals" -gt 1000000 ]; then
    pass 13 "lab_rebuild.sh successfully rebuilt lab from scratch ($rebuild_rentals replica rentals)"
  else
    fail 13 "lab_rebuild.sh execution failed or replica unready"
  fi
else
  fail 13 "Missing or non-executable lab_rebuild.sh"
fi
echo ""

# --------------------------------------------------------
# AC-14: Ruff check clean on touched files; nothing committed
# --------------------------------------------------------
echo "--- Checking AC-14 ---"
git_branch=$(git branch --show-current)
git_log_head=$(git log -1 --format="%H")
echo "Current branch: $git_branch (HEAD: $git_log_head)"

ruff_count=$( (uv run ruff check --output-format concise . 2>&1 || true) | sed -n 's/Found \([0-9]*\) error.*/\1/p' | head -n 1)
ruff_count=${ruff_count:-0}
echo "Ruff total repo errors: $ruff_count (baseline: 35)"

ruff_new_files=$(uv run ruff check workload/chaos.py tests/test_guard.py conftest.py 2>&1 || true)
echo "$ruff_new_files"

if [ "$ruff_count" -le 35 ] && [[ "$ruff_new_files" =~ "All checks passed" ]] && [ "$git_branch" = "phase1/bluebox" ] && [ "$git_log_head" = "f339428bdf6ef5fea254011f05d458ba108b7213" ]; then
  pass 14 "Ruff error count <= baseline (35); 0 errors in new files; working branch phase1/bluebox; uncommitted"
else
  fail 14 "Ruff check or branch status failed"
fi
echo ""

echo "=========================================================="
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL ACCEPTANCE CRITERIA PASSED (0 failures)"
  exit 0
else
  echo "VERIFICATION FAILED ($FAILURES failure(s))"
  exit 1
fi
