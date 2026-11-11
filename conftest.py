import os
import time

import psycopg
import pytest

LAB_PRIMARY_DSN = os.getenv(
    "LAB_PRIMARY_DSN", "postgresql://postgres:postgres@localhost:5432/bluebox"
)
LAB_REPLICA_DSN = os.getenv(
    "LAB_REPLICA_DSN", "postgresql://postgres:postgres@localhost:5433/bluebox"
)
LAB_OWNER_DSN = os.getenv(
    "LAB_OWNER_DSN", "postgresql://bluebox_owner:bluebox_owner_pw@localhost:5432/bluebox"
)
LAB_APPDB_ADMIN_DSN = os.getenv(
    "LAB_APPDB_ADMIN_DSN", "postgresql://postgres:postgres@localhost:5432/appdb"
)


def wait_for(cond, what, timeout=10, interval=0.1):
    """Poll condition periodically until true or timeout."""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for: {what}")


@pytest.fixture(autouse=True)
def clean_audit_log_before_test(request):
    """Clean the audit table in appdb as superuser before lab-dependent tests."""
    if "lab" not in request.keywords:
        yield
        return

    try:
        with psycopg.connect(LAB_APPDB_ADMIN_DSN, autocommit=True) as conn:
            conn.execute(
                "ALTER TABLE agent.audit DISABLE TRIGGER audit_no_mod; "
                "DELETE FROM agent.audit; "
                "ALTER TABLE agent.audit ENABLE TRIGGER audit_no_mod;"
            )
    except psycopg.OperationalError as e:
        pytest.fail(
            f"Lab database connection failed in clean_audit_log_before_test ({e}). "
            "Ensure the Postgres lab is running (e.g. via docker compose up -d)."
        )

    yield
