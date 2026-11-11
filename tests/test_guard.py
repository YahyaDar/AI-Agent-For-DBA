from pathlib import Path
from urllib.parse import urlparse

import pytest

from pgagent.config import settings

pytestmark = pytest.mark.pure


def test_no_postgres_superuser_in_src_pgagent():
    src_dir = Path(__file__).resolve().parent.parent / "src" / "pgagent"
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "postgres:postgres" not in content, f"Superuser credentials found in {py_file}"
        assert "rolname = 'postgres'" not in content, f"Superuser reference in {py_file}"
        for line in content.splitlines():
            line_clean = line.strip()
            if "postgresql://" in line_clean and not line_clean.startswith("#"):
                assert "postgres@" not in line_clean and "postgres:" not in line_clean, (
                    f"Superuser DSN found in {py_file}: {line_clean}"
                )


def test_settings_has_no_superuser_dsn_fields():
    """Structural check: Settings class must not define primary_dsn or replica_dsn."""
    from pgagent.config import Settings

    field_names = set(Settings.model_fields.keys())
    assert "primary_dsn" not in field_names, "Settings must not contain primary_dsn"
    assert "replica_dsn" not in field_names, "Settings must not contain replica_dsn"


def test_exec_dsn_resolves_to_agent_exec():
    parsed = urlparse(settings.exec_dsn)
    assert parsed.username == "agent_exec", (
        f"settings.exec_dsn role must be 'agent_exec', got '{parsed.username}'"
    )


def test_ro_dsn_resolves_to_agent_ro():
    parsed = urlparse(settings.ro_dsn)
    assert parsed.username == "agent_ro", (
        f"settings.ro_dsn role must be 'agent_ro', got '{parsed.username}'"
    )


def test_store_dsn_resolves_to_agent_store():
    parsed = urlparse(settings.store_dsn)
    assert parsed.username == "agent_store", (
        f"settings.store_dsn role must be 'agent_store', got '{parsed.username}'"
    )
