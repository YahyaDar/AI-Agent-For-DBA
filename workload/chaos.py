import os
import signal
import threading
import time

import psycopg

RUNNING = True


def sig_handler(signum, frame):
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, sig_handler)
signal.signal(signal.SIGINT, sig_handler)


def get_client_dsn():
    return os.getenv("LAB_OWNER_DSN", "postgresql://bluebox_owner:bluebox_owner_pw@localhost:5432/bluebox")


def run_idle_noblock(dsn):
    """Opens a transaction, runs txid_current, and stays idle in transaction without blocking anyone."""
    while RUNNING:
        try:
            with psycopg.connect(dsn, autocommit=False, application_name="workload_chaos") as conn:
                conn.execute("SELECT txid_current()")
                for _ in range(40):
                    if not RUNNING:
                        break
                    time.sleep(1)
        except psycopg.Error:
            time.sleep(1)


def run_blocker_and_waiter(dsn):
    """Creates a lock wait scenario: holder updates chaos_lock row and sleeps, waiter tries to update same row."""
    while RUNNING:
        try:
            with psycopg.connect(dsn, autocommit=False, application_name="workload_chaos") as holder:
                holder.execute("UPDATE workload.chaos_lock SET v = v + 1 WHERE id = 1")

                # Spawn waiter
                def waiter():
                    try:
                        with psycopg.connect(dsn, autocommit=False, application_name="workload_chaos") as w:
                            w.execute("UPDATE workload.chaos_lock SET v = v + 1 WHERE id = 1")
                            w.commit()
                    except psycopg.Error:
                        return

                t = threading.Thread(target=waiter, daemon=True)
                t.start()

                for _ in range(40):
                    if not RUNNING:
                        break
                    time.sleep(1)
                holder.rollback()
                t.join(timeout=2)
        except psycopg.Error:
            time.sleep(1)


def main():
    dsn = get_client_dsn()
    threads = [
        threading.Thread(target=run_idle_noblock, args=(dsn,), daemon=True),
        threading.Thread(target=run_idle_noblock, args=(dsn,), daemon=True),
        threading.Thread(target=run_blocker_and_waiter, args=(dsn,), daemon=True),
    ]
    for t in threads:
        t.start()

    while RUNNING:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
