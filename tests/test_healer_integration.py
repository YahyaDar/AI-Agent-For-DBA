import time
from pgagent.config import settings
from pgagent.healer import run_cycle
from pgagent.tools.db import ro_conn
from scenarios import bloat

def _dead(ro):
    return ro.execute("SELECT n_dead_tup::bigint AS d FROM pg_stat_user_tables "
                      "WHERE relname = 'bloat_test'").fetchone()["d"]

def test_healer_actually_fixes_bloat(monkeypatch):
    monkeypatch.setattr(settings, "cooldown_s", 0)   # don't skip because of earlier audit rows
    bloat.inject()
    time.sleep(1)
    with ro_conn() as ro:
        assert _dead(ro) > 100_000
    run_cycle()
    time.sleep(1)
    with ro_conn() as ro:
        assert _dead(ro) < 1_000
    bloat.cleanup()
