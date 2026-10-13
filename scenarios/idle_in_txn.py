import time
from .common import connect, kill_scenario_sessions

def inject(duration=300, count=0):
    c = connect(autocommit=False, app="scenario_idle_txn")
    c.execute("SELECT txid_current()")   # forces an xid, session now sits 'idle in transaction'
    print(f"Idle-in-transaction session held for {duration}s. Ctrl-C to release.")
    try:
        time.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        try: c.rollback(); c.close()
        except Exception: pass

def cleanup():
    kill_scenario_sessions()
