-- scripts/setup.sql: Roles, databases, and grants for pgagent and Bluebox
\set ON_ERROR_STOP on

-- 1. CLUSTER-LEVEL ROLES
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bluebox_owner') THEN
    CREATE ROLE bluebox_owner LOGIN PASSWORD 'bluebox_owner_pw';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_ro') THEN
    CREATE ROLE agent_ro LOGIN PASSWORD 'agent_ro_pw';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_exec') THEN
    CREATE ROLE agent_exec LOGIN PASSWORD 'agent_exec_pw';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_store') THEN
    CREATE ROLE agent_store LOGIN PASSWORD 'agent_store_pw';
  END IF;
END $$;

-- Role attributes & global grants
GRANT pg_monitor TO agent_ro;
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '5s';

-- agent_exec: non-superuser, maintenance + process signaling + reading activity stats
ALTER ROLE agent_exec NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT pg_maintain TO agent_exec;
GRANT pg_signal_backend TO agent_exec;
GRANT pg_read_all_stats TO agent_exec;

-- agent_store: non-superuser
ALTER ROLE agent_store NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- Ensure database bluebox exists
SELECT 'CREATE DATABASE bluebox OWNER bluebox_owner'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'bluebox')\gexec

-- Database connection privileges
REVOKE CONNECT ON DATABASE bluebox FROM PUBLIC;
GRANT CONNECT ON DATABASE bluebox TO bluebox_owner, agent_ro, agent_exec;

REVOKE CONNECT ON DATABASE appdb FROM PUBLIC;
GRANT CONNECT ON DATABASE appdb TO agent_ro, agent_store;

-- 2. APPDB SETUP (agent store & audit log)
\c appdb

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgstattuple;

CREATE SCHEMA IF NOT EXISTS agent;
CREATE TABLE IF NOT EXISTS agent.audit (
  id           uuid PRIMARY KEY,
  ts           timestamptz NOT NULL,
  finding_type text NOT NULL,
  target       text NOT NULL,
  action_id    text,
  verdict      text NOT NULL,
  tier         text NOT NULL,
  status       text,
  record       jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_ts_idx ON agent.audit (ts DESC);

CREATE OR REPLACE FUNCTION agent.audit_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'agent.audit is append-only'; END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_no_mod ON agent.audit;
CREATE TRIGGER audit_no_mod BEFORE UPDATE OR DELETE ON agent.audit
  FOR EACH ROW EXECUTE FUNCTION agent.audit_immutable();

-- Store role grants on audit table
GRANT USAGE ON SCHEMA agent TO agent_store;
GRANT INSERT, SELECT ON agent.audit TO agent_store;

-- agent_ro does NOT need access to agent.audit (verified by pure/lab test suite)

-- 3. BLUEBOX SETUP
\c bluebox

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgstattuple;

-- bluebox_owner owns public schema and bluebox database
GRANT ALL ON SCHEMA public TO bluebox_owner;
GRANT ALL ON DATABASE bluebox TO bluebox_owner;

-- Allow agent_exec and agent_ro to resolve objects in schemas
GRANT USAGE ON SCHEMA public TO agent_exec, agent_ro;

DO $$ BEGIN
  IF EXISTS (SELECT FROM information_schema.schemata WHERE schema_name = 'bluebox') THEN
    EXECUTE 'GRANT USAGE ON SCHEMA bluebox TO agent_exec, agent_ro';
  END IF;
  IF EXISTS (SELECT FROM information_schema.schemata WHERE schema_name = 'staging') THEN
    EXECUTE 'GRANT USAGE ON SCHEMA staging TO agent_exec, agent_ro';
  END IF;
  IF EXISTS (SELECT FROM information_schema.schemata WHERE schema_name = 'workload') THEN
    EXECUTE 'GRANT USAGE ON SCHEMA workload TO agent_exec, agent_ro';
  END IF;
END $$;
