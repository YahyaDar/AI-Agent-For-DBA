import pytest
from pgagent.executor.actions import validate_ast
from pgagent.policy import evaluate
from pgagent.schemas import ActionId, Finding, ProposedAction, Verdict

F = Finding(type="table_bloat", severity="warning", target="public.x", evidence={})

def test_injection_in_params_is_denied():
    a = ProposedAction(finding_id=F.id, action_id="vacuum_table", proposed_by="llm",
                       params={"schema_name": "public", "table": "x; DROP TABLE y"})
    assert evaluate(a, F, None).verdict == Verdict.DENY      # denied before touching the DB

def test_extra_params_rejected():
    a = ProposedAction(finding_id=F.id, action_id="vacuum_table", proposed_by="llm",
                       params={"schema_name": "public", "table": "x", "full": True})
    assert evaluate(a, F, None).verdict == Verdict.DENY

def test_ast_rejects_wrong_statement():
    with pytest.raises(ValueError):
        validate_ast("DROP TABLE x", ActionId.VACUUM_TABLE)
    with pytest.raises(ValueError):
        validate_ast("VACUUM x; DROP TABLE y", ActionId.VACUUM_TABLE)
    validate_ast('VACUUM "public"."x"', ActionId.VACUUM_TABLE)
