from .common import connect, drop_tables

TABLE = "public.stale_test"

def inject(duration=0, count=0, new_rows=500_000):
    with connect() as c:
        c.execute(f"DROP TABLE IF EXISTS {TABLE}")
        c.execute(f"CREATE TABLE {TABLE} (id int, grp int)")
        c.execute(f"CREATE INDEX ON {TABLE} (grp)")
        c.execute(f"ALTER TABLE {TABLE} SET (autovacuum_enabled = false)")
        c.execute(f"INSERT INTO {TABLE} SELECT g, mod(g, 100) FROM generate_series(1, 1000) g")
        c.execute(f"ANALYZE {TABLE}")
        # skew the data after the last ANALYZE so the planner's stats are wrong
        c.execute(f"INSERT INTO {TABLE} SELECT g, 1 FROM generate_series(1, %s) g", (new_rows,))
    print(f"Injected stale stats on {TABLE}.")

def cleanup():
    drop_tables(TABLE)
