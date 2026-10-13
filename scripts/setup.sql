-- read-only agent role: no write privileges on user tables, enforced by privileges
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_ro') THEN
    CREATE ROLE agent_ro LOGIN PASSWORD 'agent_ro_pw';
  END IF;
END $$;
GRANT pg_monitor TO agent_ro;
GRANT CONNECT ON DATABASE appdb TO agent_ro;
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '5s';

-- audit log (append-only)
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
CREATE OR REPLACE TRIGGER audit_no_mod BEFORE UPDATE OR DELETE ON agent.audit
  FOR EACH ROW EXECUTE FUNCTION agent.audit_immutable();

GRANT USAGE ON SCHEMA agent TO agent_ro;
GRANT SELECT ON agent.audit TO agent_ro;
