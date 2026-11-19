"""Running token-usage estimate, from the per-call log written by ai.py."""
import json

from . import db
from .ai import USAGE_LOG

ESTIMATE_PER_APP = 22_000  # rough pre-measurement estimate: tailor + question matching + drafts


def summary():
    rows = [json.loads(x) for x in USAGE_LOG.read_text().splitlines() if x.strip()] if USAGE_LOG.exists() else []
    tok = lambda r: r["input"] + r["cache_read"] + r["cache_write"] + r["output"]
    by = {}
    for r in rows:
        b = by.setdefault(r["model"], {"calls": 0, "tokens": 0})
        b["calls"] += 1
        b["tokens"] += tok(r)
    total = sum(tok(r) for r in rows)
    with db.conn() as c:
        apps = c.execute("SELECT COUNT(*) FROM jobs WHERE pdf_path IS NOT NULL").fetchone()[0]
    per = total / apps if apps else None
    return dict(calls=len(rows), total=total, by_model=by, apps=apps, per_app=per,
                per_100=(per * 100 if per else ESTIMATE_PER_APP * 100), measured=bool(per))


def text():
    s = summary()
    if not s["calls"]:
        return f"Usage: nothing used yet. Pre-run estimate ~{ESTIMATE_PER_APP:,} tokens per application (~{ESTIMATE_PER_APP * 100 / 1e6:.1f}M per 100)."
    models = ", ".join(f"{m}: {b['calls']} calls / {b['tokens']:,}" for m, b in s["by_model"].items())
    per = f"{s['per_app']:,.0f} tokens per tailored application so far" if s["apps"] else "no tailored application yet"
    return (f"Usage so far: {s['total']:,} tokens in {s['calls']} calls ({models}). {per}. "
            f"Projected for 100 applications: ~{s['per_100'] / 1e6:.1f}M tokens"
            f"{'' if s['measured'] else ' (estimate)'}.")
