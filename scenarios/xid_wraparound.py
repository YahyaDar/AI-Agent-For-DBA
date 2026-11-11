from .common import connect, drop_tables

TABLE = "public.xid_test"


def inject(duration=0, count=0):
    with connect() as c:
        c.execute(f"DROP TABLE IF EXISTS {TABLE}")
        c.execute(f"CREATE TABLE {TABLE} (id int PRIMARY KEY, payload text)")
        c.execute(f"ALTER TABLE {TABLE} SET (autovacuum_enabled = false)")
        c.execute(f"INSERT INTO {TABLE} SELECT g, md5(g::text) FROM generate_series(1, 100) g")
        c.execute(f"VACUUM (FREEZE) {TABLE}")
        c.execute(
            f"UPDATE pg_class SET relfrozenxid = ((txid_current() - 150000000) & 4294967295)::text::xid "
            f"WHERE oid = '{TABLE}'::regclass"
        )
    print(f"Injected xid_wraparound into {TABLE} (age ~150M).")


def cleanup():
    drop_tables(TABLE)
