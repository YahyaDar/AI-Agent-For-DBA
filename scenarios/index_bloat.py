from .common import connect, drop_tables

TABLE = "public.index_bloat_test"


def inject(duration=0, count=0):
    with connect() as c:
        c.execute(f"DROP TABLE IF EXISTS {TABLE}")
        c.execute(f"CREATE TABLE {TABLE} (id int PRIMARY KEY, payload text)")
        c.execute(f"ALTER TABLE {TABLE} SET (autovacuum_enabled = false)")
        c.execute(
            f"INSERT INTO {TABLE} SELECT g, repeat(md5(g::text), 4) FROM generate_series(1, 20000) g"
        )
        c.execute(f"CREATE INDEX idx_bloat_test_payload ON {TABLE} (payload)")
        c.execute(f"DELETE FROM {TABLE} WHERE mod(id, 5) <> 0")
        c.execute(f"VACUUM {TABLE}")
    print(f"Injected index_bloat into {TABLE} (sparse B-tree index ~80% bloat).")


def cleanup():
    drop_tables(TABLE)
