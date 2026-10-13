import argparse, importlib

NAMES = ["bloat", "stale_stats", "idle_in_txn", "replication_lag"]

def main():
    p = argparse.ArgumentParser(description="Inject or clean up a Postgres fault scenario")
    p.add_argument("scenario", choices=NAMES + ["all"])
    p.add_argument("action", choices=["inject", "cleanup"])
    p.add_argument("--duration", type=int, default=120, help="seconds to hold session-based faults")
    p.add_argument("--count", type=int, default=3)
    a = p.parse_args()
    for name in (NAMES if a.scenario == "all" else [a.scenario]):
        mod = importlib.import_module(f"scenarios.{name}")
        if a.action == "inject":
            mod.inject(duration=a.duration, count=a.count)
        else:
            mod.cleanup()
        print(f"[{name}] {a.action} done")

if __name__ == "__main__":
    main()
