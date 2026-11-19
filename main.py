import argparse
import sys

from jobbot import db


def main():
    ap = argparse.ArgumentParser(description="Job application assistant with approval queue")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("app", help="open the web interface (paste links, approve, answer questions)")
    sub.add_parser("fetch", help="auto-discover jobs on the ATS boards in config.yaml, score, tailor, queue")
    sub.add_parser("kpis", help="suggest questions to uncover real metrics for your roles")
    sub.add_parser("stats", help="counts by status")
    args = ap.parse_args()

    if args.cmd == "app":
        from jobbot.webapp import run
        run()
    elif args.cmd == "fetch":
        from jobbot.pipeline import run_fetch
        run_fetch()
    elif args.cmd == "stats":
        print(db.counts())
        import json
        from jobbot.ai import USAGE_LOG
        rows = [json.loads(x) for x in USAGE_LOG.read_text().splitlines()] if USAGE_LOG.exists() else []
        for m in sorted({r["model"] for r in rows}):
            rs = [r for r in rows if r["model"] == m]
            print(f"{m}: {len(rs)} calls, input {sum(r['input'] + r['cache_read'] + r['cache_write'] for r in rs):,}"
                  f" (cached {sum(r['cache_read'] for r in rs):,}), output {sum(r['output'] for r in rs):,} tokens")
    elif args.cmd == "kpis":
        from jobbot import ai
        from jobbot.config import load_master
        for r in ai.kpi_ideas(load_master()[1])["roles"]:
            print(f"\n{r['company']}")
            for q in r["questions"]:
                print(f"  - {q}")


if __name__ == "__main__":
    sys.exit(main())
