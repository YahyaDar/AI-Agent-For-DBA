import psycopg
from pgagent.config import settings

def connect(replica=False, autocommit=True, app="scenario"):
    dsn = settings.replica_dsn if replica else settings.primary_dsn
    return psycopg.connect(dsn, autocommit=autocommit, application_name=app)

def kill_scenario_sessions():
    """Terminate any leftover scenario sessions so DROP TABLE can't block."""
    with connect() as c:
        c.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE application_name LIKE 'scenario_%' AND pid <> pg_backend_pid()"
        )

def drop_tables(*tables):
    kill_scenario_sessions()
    with connect() as c:
        for t in tables:
            c.execute(f"DROP TABLE IF EXISTS {t}")
