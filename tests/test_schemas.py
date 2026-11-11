import pytest
from pydantic import ValidationError

from pgagent.schemas import ProposedAction

pytestmark = pytest.mark.pure


def test_rejects_unknown_action():
    with pytest.raises(ValidationError):
        ProposedAction(
            finding_id="00000000-0000-0000-0000-000000000000",
            action_id="drop_table",
            proposed_by="llm",
        )
