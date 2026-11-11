import pytest

from conftest import wait_for
from pgagent.config import settings
from pgagent.healer import run_cycle
from pgagent.tools.db import ro_conn
from scenarios import bloat
from scenarios.common import kill_scenario_sessions

pytestmark = pytest.mark.lab


def _dead(ro):
    return ro.execute(
        "SELECT n_dead_tup::bigint AS d FROM pg_stat_user_tables "
        "WHERE relname = 'bloat_test'"
    ).fetchone()["d"]


def test_healer_actually_fixes_bloat(monkeypatch):
    monkeypatch.setattr(settings, "cooldown_s", 0)  # don't skip because of earlier audit rows

    # Terminate any leftover scenario or workload sessions before injecting bloat.
    # Open transactions would hold back the OldestXmin horizon, causing Vacuum Futility
    # where dead rows cannot be pruned by VACUUM.
    kill_scenario_sessions()

    # Fault injection tuple math:
    # 200,000 inserted rows + (3 updates * 200,000 = 600,000 dead) + (100,000 deleted = 100,000 dead)
    # Total dead tuples: ~700,000; live: 100,000; dead ratio: 700k / 800k = 87.5%,
    # far exceeding settings.bloat_dead_ratio (0.20) and settings.bloat_min_dead (10,000).
    bloat.inject()

    with ro_conn() as ro:
        wait_for(
            lambda: _dead(ro) > settings.bloat_min_dead,
            "bloat tuples to reflect in pg_stat_user_tables above bloat_min_dead threshold",
        )
        assert _dead(ro) > settings.bloat_min_dead

    run_cycle()

    with ro_conn() as ro:
        wait_for(
            lambda: _dead(ro) < settings.bloat_min_dead,
            "vacuum_table to clear dead tuples below bloat_min_dead threshold",
        )
        assert _dead(ro) < settings.bloat_min_dead

    bloat.cleanup()
