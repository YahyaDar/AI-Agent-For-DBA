ALTER TABLE agent.audit DISABLE TRIGGER audit_no_mod;
DELETE FROM agent.audit;
ALTER TABLE agent.audit ENABLE TRIGGER audit_no_mod;
