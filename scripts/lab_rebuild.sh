#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== STEP 1: TEARING DOWN LAB VOLUMES ==="
docker compose -f "$REPO_DIR/compose.yaml" down -v

echo "=== STEP 2: BUILDING AND STARTING CONTAINERS ==="
docker compose -f "$REPO_DIR/compose.yaml" up -d --build

echo "=== STEP 3: WAITING FOR PRIMARY HEALTHCHECK ==="
until docker compose -f "$REPO_DIR/compose.yaml" exec -T primary pg_isready -U postgres -d appdb >/dev/null 2>&1; do
  sleep 1
done
echo "Primary is ready."

echo "=== STEP 4: WAITING FOR REPLICA RECOVERY ==="
sleep 3
until [ "$(docker compose -f "$REPO_DIR/compose.yaml" exec -T replica psql -U postgres -d appdb -tAc "SELECT pg_is_in_recovery();" 2>/dev/null)" = "t" ]; do
  sleep 1
done
echo "Replica is in recovery."

echo "=== STEP 5: RUNNING INITIAL SETUP SQL ==="
docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -v ON_ERROR_STOP=1 < "$REPO_DIR/scripts/setup.sql"

echo "=== STEP 6: LOADING BLUEBOX SCHEMA & DATA ==="
if [ -f "$REPO_DIR/vendor/bluebox/bluebox_schema.sql" ]; then
  echo "Loading bluebox_schema.sql..."
  docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -v ON_ERROR_STOP=1 < "$REPO_DIR/vendor/bluebox/bluebox_schema.sql" >/dev/null
fi

echo "Setting object ownership to bluebox_owner..."
docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox << 'EOF' >/dev/null
DO $$
DECLARE r record;
BEGIN
  FOR r IN (SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('bluebox', 'staging', 'topology') OR (schemaname = 'public' AND tablename = 'flyway_schema_history')) LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO bluebox_owner;', r.schemaname, r.tablename);
  END LOOP;
  FOR r IN (SELECT sequence_schema, sequence_name FROM information_schema.sequences WHERE sequence_schema IN ('bluebox', 'staging', 'topology')) LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO bluebox_owner;', r.sequence_schema, r.sequence_name);
  END LOOP;
  FOR r IN (SELECT schema_name FROM information_schema.schemata WHERE schema_name IN ('bluebox', 'staging', 'topology')) LOOP
    EXECUTE format('ALTER SCHEMA %I OWNER TO bluebox_owner;', r.schema_name);
  END LOOP;
END $$;
EOF

if [ -f "$REPO_DIR/vendor/bluebox/bluebox_data.sql.gz" ]; then
  echo "Loading bluebox_data.sql.gz..."
  zcat "$REPO_DIR/vendor/bluebox/bluebox_data.sql.gz" | docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -d bluebox -v ON_ERROR_STOP=1 >/dev/null
fi

echo "Analyzing bluebox..."
docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U bluebox_owner -d bluebox -c "ANALYZE;" >/dev/null

echo "=== STEP 7: RE-APPLYING SETUP & SCHEMA GRANTS ==="
docker compose -f "$REPO_DIR/compose.yaml" exec -T primary psql -U postgres -v ON_ERROR_STOP=1 < "$REPO_DIR/scripts/setup.sql" >/dev/null

echo "=== STEP 8: INITIALIZING WORKLOAD ENVIRONMENT ==="
"$REPO_DIR/workload/run.sh" reset >/dev/null

echo "=== LAB REBUILD COMPLETE ==="
