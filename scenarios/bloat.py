from .common import connect, drop_tables

TABLE = "public.bloat_test"

def inject(duration=0, count=0, rows=200_000):
    with connect() as c:
        c.execute(f"DROP TABLE IF EXISTS {TABLE}")
        c.execute(f"CREATE TABLE {TABLE} (id int PRIMARY KEY, payload text, n int)")
        c.execute(f"ALTER TABLE {TABLE} SET (autovacuum_enabled = false)")
        c.execute(f"INSERT INTO {TABLE} SELECT g, md5(g::text), 0 FROM generate_series(1, %s) g", (rows,))
        for _ in range(3):
            c.execute(f"UPDATE {TABLE} SET n = n + 1")
        c.execute(f"DELETE FROM {TABLE} WHERE mod(id, 2) = 0")
    print(f"Injected bloat into {TABLE} (autovacuum disabled on it).")

def cleanup():
    drop_tables(TABLE)
