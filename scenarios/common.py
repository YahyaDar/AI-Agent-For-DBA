import os

import psycopg

LAB_PRIMARY_DSN = os.getenv(
    "LAB_PRIMARY_DSN", "postgresql://postgres:postgres@localhost:5432/bluebox"
)
LAB_REPLICA_DSN = os.getenv(
    "LAB_REPLICA_DSN", "postgresql://postgres:postgres@localhost:5433/bluebox"
)


def connect(replica=False, autocommit=True, app="scenario"):
    dsn = LAB_REPLICA_DSN if replica else LAB_PRIMARY_DSN
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
