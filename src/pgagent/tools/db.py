import psycopg
from psycopg.rows import dict_row
from pgagent.config import settings

def ro_conn() -> psycopg.Connection:
    """Read-only connection as agent_ro. Can't write: the role has no write privileges."""
    return psycopg.connect(settings.ro_dsn, autocommit=True, row_factory=dict_row,
                           application_name="pgagent_ro")
