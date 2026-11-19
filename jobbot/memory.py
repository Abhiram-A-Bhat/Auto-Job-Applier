"""Answer memory: ask once, remember forever (profile/answers.json, editable at /memory in the app)."""
import difflib
import json
import re
import time

from .config import ROOT, cfg

PATH = ROOT / "profile" / "answers.json"


def norm(label):
    s = re.sub(r"\(required\)|required|\*", " ", str(label).lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save(d):
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def remember(label, answer, kind="text"):
    d = load()
    d[norm(label)] = {"label": label, "answer": answer, "kind": kind, "updated": time.time()}
    save(d)


def forget(key):
    d = load()
    d.pop(key, None)
    save(d)


def _semantic(label, options, d):
    """Cheap Haiku call: is this the same question as one we've already answered?"""
    from . import ai
    keys = list(d)
    listing = "\n".join(f"{i}: {d[k]['label']} => {str(d[k]['answer'])[:60]}" for i, k in enumerate(keys))
    sys = [{"type": "text", "text": (
        "Match a new job-application form question to a previously answered one ONLY if both ask for exactly "
        "the same fact. Country, company, year, role and time period must be identical (e.g. authorization to "
        "work in India is NOT the same as in the US). If unsure answer null. "
        'JSON only: {"match": index or null}')}]
    try:
        r = ai._call(cfg["models"]["score"], sys,
                     f"NEW QUESTION: {label}\nOPTIONS: {options}\n\nREMEMBERED:\n{listing}", 60)
        m = r.get("match")
        return d[keys[m]]["answer"] if isinstance(m, int) and 0 <= m < len(keys) else None
    except Exception:
        return None


def lookup(label, options=None):
    d = load()
    k = norm(label)
    if not k or not d:
        return None
    if k in d:
        return d[k]["answer"]
    best = difflib.get_close_matches(k, list(d), n=1, cutoff=0.9)
    if best:
        return d[best[0]]["answer"]
    return _semantic(label, options, d)


def _has(a, o):
    return bool(a) and re.search(rf"\b{re.escape(a)}\b", o) is not None


def pick_option(answer, options):
    """Map a remembered answer onto one of this form's option strings (or None)."""
    a = norm(answer)
    for o in options:
        if norm(o) == a:
            return o
    for o in options:
        if _has(a, norm(o)) or _has(norm(o), a):
            return o
    m = difflib.get_close_matches(a, [norm(o) for o in options], n=1, cutoff=0.6)
    return next((o for o in options if norm(o) == m[0]), None) if m else None
