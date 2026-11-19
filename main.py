import argparse
import sys

from jobbot import db


def main():
    ap = argparse.ArgumentParser(description="Job application assistant with approval queue")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("app", help="open the web interface (paste links, approve, answer questions)")
    sub.add_parser("fetch", help="auto-discover jobs on the ATS boards in config.yaml, score, tailor, queue")
    sub.add_parser("kpis", help="suggest questions to uncover real metrics for your roles")
    sub.add_parser("stats", help="job counts and running token usage")
    sl = sub.add_parser("set-login", help="set the web app's login (password is stored only as a hash)")
    sl.add_argument("--email", required=True)
    args = ap.parse_args()

    if args.cmd == "set-login":
        import getpass
        from jobbot.webapp import set_login
        pw = getpass.getpass("New password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\r\n")
        set_login(args.email, pw)
        print("Login saved. Restart the app; all devices will need to log in again.")
        return

    if args.cmd == "app":
        from jobbot.webapp import run
        run()
    elif args.cmd == "fetch":
        from jobbot.pipeline import run_fetch
        run_fetch()
    elif args.cmd == "stats":
        from jobbot import usage
        print(db.counts())
        print(usage.text())
    elif args.cmd == "kpis":
        from jobbot import ai
        from jobbot.config import load_master
        for r in ai.kpi_ideas(load_master()[1])["roles"]:
            print(f"\n{r['company']}")
            for q in r["questions"]:
                print(f"  - {q}")


if __name__ == "__main__":
    sys.exit(main())
