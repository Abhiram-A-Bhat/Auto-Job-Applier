import json
import re
import threading
import time

import httpx

from . import ai, db, fetchjob, sources
from .config import ROOT, cfg, load_master
from .pdfgen import build_pdf, safe_name

_busy = threading.Lock()


def tailor_job(job, master, master_txt):
    if job["company"] == "Unknown":  # page read from a job site: clean title/company/description first (Haiku)
        info = ai.extract_job(job["description"])
        db.update(job["id"], **{k: info[k] for k in ("title", "company", "location", "description") if info.get(k)})
        job = db.get(job["id"])
    res = ai.tailor(job, master_txt)
    resume, cover, warns = ai.ground(res, master, master_txt)
    out = ROOT / "data" / "resumes"
    out.mkdir(parents=True, exist_ok=True)
    pdf = build_pdf(resume, master["contact"], out / f"{safe_name(master['contact']['name'], job['company'], job['title'])}.pdf")
    db.update(job["id"], status="queued", resume_json=json.dumps(resume), cover=cover, pdf_path=str(pdf),
              warnings=json.dumps(warns))


OFFLINE = re.compile(r"getaddrinfo|ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|ERR_NETWORK_CHANGED|"
                     r"Name or service not known|Temporary failure in name resolution", re.I)


def _retry_offline(fn, say, waits=(10, 20, 30, 60, 60, 120, 120, 180, 300)):
    """Internet/DNS blips shouldn't fail a link: wait for the connection to come back (up to ~15 min)."""
    for w in waits:
        try:
            return fn()
        except Exception as e:
            if not (isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)) or OFFLINE.search(str(e))):
                raise
            say(f"No internet connection - retrying in {w}s...")
            time.sleep(w)
    return fn()


def process_link(url, pasted="", say=print):
    """Pasted link (local app or cloud inbox) -> job read -> tailored -> queued. Returns (job_id, message)."""
    master, txt = load_master()
    job = _retry_offline(lambda: fetchjob.resolve(url, pasted, say=say), say)
    db.insert_job({**job, "status": "processing"})
    cur = db.get(job["id"])
    if cur["status"] not in ("processing", "new", "scored", "skipped", "error", "rejected"):
        return job["id"], f"Already in the system (status: {cur['status']})."
    db.update(job["id"], status="processing", description=job["description"])
    tailor_job(db.get(job["id"]), master, txt)
    return job["id"], f"Queued for approval: {job['title']} - {job['company']}"


def overlap(job, skills):
    """Free, local relevance check: how many of your skills appear in the posting."""
    text = (job["title"] + " " + job["description"]).lower()
    n = 0
    for s in skills:
        name = re.escape(s.lower().split(" (")[0])
        if re.search(rf"(?<![a-z0-9]){name}(?![a-z0-9])", text):
            n += 1
    return n


def _enrich(job):
    """Aggregator links (Adzuna) -> follow to the real posting: full description + real apply URL."""
    try:
        final = str(httpx.get(job["url"], follow_redirects=True, timeout=20,
                              headers={"User-Agent": "Mozilla/5.0"}).url)
    except Exception:
        return job
    if fetchjob.is_manual(final):  # lands on LinkedIn/Indeed/...: tailor only, you apply yourself
        db.update(job["id"], source="manual", url=final, apply_url=final)
    else:
        fields = {"url": final, "apply_url": final}
        try:
            full = fetchjob.resolve(final)
            fields["apply_url"] = full["apply_url"]
            if len(full["description"]) > len(job["description"]):
                fields["description"] = full["description"]
        except Exception:
            pass
        db.update(job["id"], **fields)
    return db.get(job["id"])


def _discover_platforms(say):
    """LinkedIn/Naukri/Indeed/Wellfound via your logged-in browser (see platforms.py for the ground rules)."""
    from . import formfill, platforms  # lazy: needs Playwright
    found = 0
    for name in [n for n in platforms.PLATFORMS if n in platforms.enabled()]:
        known = db.known_urls(name)
        try:
            rows = formfill.run_in_browser(lambda b, n=name: platforms.discover(b, n, say, known))
        except Stopped:
            raise
        except Exception as e:
            say(f"  ! {name}: {str(e)[:120]}")
            continue
        for r in rows:
            j = platforms.to_job(name, r)
            if sources._wanted(j, check_loc=False) and sources.salary_ok(j):  # searches are already Bengaluru/remote
                db.insert_job(j)
                found += 1
    return found


class Stopped(Exception):
    pass


STOP = threading.Event()  # set by the Stop button; checked at every step and during every browser wait


def check_stop():
    if STOP.is_set():
        raise Stopped("Stopped.")


def searching():
    return _busy.locked()


def run_fetch(say=print):
    """Discover -> free local filter -> cheap LLM score of the best few -> tailor -> queue."""
    if not _busy.acquire(blocking=False):
        return say("A job search is already running.")
    STOP.clear()
    raw_say = say

    def say(m):
        check_stop()
        raw_say(m)

    try:
        master, master_txt = load_master()
        s = cfg["search"]
        say("Searching job sources...")
        found = 0
        for j in sources.fetch_all():
            db.insert_job(j)
            found += 1
        found += _discover_platforms(say)
        say(f"{found} jobs matched your title/location/salary filters.")
        cands = []
        for job in db.by_status("new"):
            o = overlap(job, master["skills"])
            if o < s.get("min_skill_overlap", 2):
                db.update(job["id"], status="skipped", score_reason=f"only {o} of your skills mentioned")
            else:
                cands.append((o, job))
        cands.sort(key=lambda x: -x[0])
        for n, (_, job) in enumerate(cands[: s.get("max_score_per_run", 20)], 1):
            say(f"Scoring {n}: {job['title']} - {job['company']}")
            try:
                r = ai.score(job, master_txt)
            except Stopped:
                raise
            except Exception as e:
                say(f"  ! {e}")
                continue
            db.update(job["id"], status="scored" if r["score"] >= cfg["min_score"] else "skipped",
                      score=r["score"], score_reason=r["reason"])
        run_tailor(master, master_txt, say)
        say("Search finished.")
    finally:
        STOP.clear()  # a Stop only ends this search; it must not affect pasted links or the next search
        _busy.release()


def run_tailor(master=None, master_txt=None, say=print):
    if master is None:
        master, master_txt = load_master()
    todo = db.by_status("scored", "score DESC")[: cfg["max_tailor_per_run"]]
    for job in todo:
        say(f"Tailoring: {job['title']} - {job['company']} (fit {job['score']})")
        try:
            if job["source"] == "adzuna":
                job = _enrich(job)
            tailor_job(job, master, master_txt)
            job = db.get(job["id"])
            thr = cfg.get("auto_approve_min_score", 0)
            if thr and job["source"] != "manual" and (job["score"] or 0) >= thr:
                from . import formfill  # imported lazily: needs Playwright
                db.update(job["id"], status="approved")
                formfill.enqueue(job["id"])
        except Stopped:
            raise
        except Exception as e:
            db.update(job["id"], status="error", notes=str(e))
            say(f"  ! {e}")
    say(f"Queued {len(todo)} for your approval.")
