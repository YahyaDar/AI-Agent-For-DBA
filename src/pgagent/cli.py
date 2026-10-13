import argparse
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from pgagent.audit.store import list_recent
from pgagent.checks import run_all_checks
from pgagent.config import settings
from pgagent.healer import run_cycle
from pgagent.tools.db import ro_conn

def main():
    p = argparse.ArgumentParser(prog="pgagent")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="detect only, change nothing")
    o = sub.add_parser("once", help="run one detect -> gate -> act cycle")
    o.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("run", help="run the cycle on a schedule")
    r.add_argument("--every", type=int, default=30, help="seconds between cycles")
    r.add_argument("--dry-run", action="store_true")
    a = sub.add_parser("audit", help="show recent audit records")
    a.add_argument("-n", type=int, default=20)
    args = p.parse_args()

    if getattr(args, "dry_run", False):
        settings.dry_run = True

    if args.cmd == "check":
        with ro_conn() as c:
            fs = run_all_checks(c)
        for f in fs:
            print(f"{f.severity:8} {f.type.value:16} {f.target:28} {f.evidence}")
        print(f"{len(fs)} finding(s)")
    elif args.cmd == "once":
        run_cycle()
    elif args.cmd == "run":
        sched = BlockingScheduler()
        sched.add_job(run_cycle, "interval", seconds=args.every, next_run_time=datetime.now(), max_instances=1,
                      misfire_grace_time=30, coalesce=True)
        print(f"healer running every {args.every}s (dry_run={settings.dry_run}). Ctrl-C to stop.")
        try:
            sched.start()
        except (KeyboardInterrupt, SystemExit):
            pass
    elif args.cmd == "audit":
        with ro_conn() as c:
            for r_ in list_recent(c, args.n):
                print(f"{r_['ts']:%H:%M:%S} {r_['verdict']:8} {r_['tier']:6} {r_['finding_type']:16} "
                      f"{r_['target']:26} {str(r_['action_id'] or '-'):22} {r_['status'] or '-'}  {r_['reasons']}")

if __name__ == "__main__":
    main()
