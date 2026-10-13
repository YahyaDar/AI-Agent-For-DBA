import time
from .common import connect

TABLE = "public.lag_test"
STATUS_SQL = (
    "SELECT application_name, state, "
    "pg_wal_lsn_diff(sent_lsn, replay_lsn) AS lag_bytes, replay_lag FROM pg_stat_replication"
)

def inject(duration=60, count=0):
    with connect(replica=True) as r:
        r.execute("SELECT pg_wal_replay_pause()")      # replica stops applying WAL
    with connect() as p:
        p.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (id serial, payload text)")
        end = time.time() + duration
        while time.time() < end:
            p.execute(f"INSERT INTO {TABLE}(payload) SELECT md5(random()::text) FROM generate_series(1, 5000)")
            time.sleep(0.5)
        print(p.execute(STATUS_SQL).fetchall())
    print("Replica replay is PAUSED. Run cleanup to resume.")

def cleanup():
    with connect(replica=True) as r:
        r.execute("SELECT pg_wal_replay_resume()")
    with connect() as p:
        p.execute(f"DROP TABLE IF EXISTS {TABLE}")
