#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<-EOSQL
  CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replpass';
  SELECT pg_create_physical_replication_slot('replica_slot');
  CREATE EXTENSION IF NOT EXISTS vector;
  CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
  CREATE EXTENSION IF NOT EXISTS pgstattuple;
EOSQL
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
