from dataclasses import dataclass
from pydantic import Field
from pgagent.schemas import ActionId, FindingType, RiskTier, Strict

IDENT = r"^[A-Za-z_][A-Za-z0-9_]*$"
PROTECTED_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast", "agent"}

class TableParams(Strict):
    schema_name: str = Field(pattern=IDENT, max_length=63)
    table: str = Field(pattern=IDENT, max_length=63)

class PidParams(Strict):
    pid: int = Field(gt=0)

@dataclass(frozen=True)
class ActionSpec:
    tier: RiskTier
    params: type[Strict]
    finding_type: FindingType      # the only finding type that may justify this action

# Whitelist. Anything not listed is denied.
CATALOG: dict[ActionId, ActionSpec] = {
    ActionId.ANALYZE_TABLE:        ActionSpec(RiskTier.LOW,    TableParams, FindingType.STALE_STATS),
    ActionId.VACUUM_TABLE:         ActionSpec(RiskTier.LOW,    TableParams, FindingType.TABLE_BLOAT),
    ActionId.REINDEX_CONCURRENTLY: ActionSpec(RiskTier.MEDIUM, TableParams, FindingType.INDEX_BLOAT),
    ActionId.TERMINATE_BACKEND:    ActionSpec(RiskTier.MEDIUM, PidParams,   FindingType.IDLE_IN_TXN),
}
