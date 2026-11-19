import json

from . import ai, db, sources
from .config import ROOT, cfg, load_master
from .pdfgen import build_pdf, safe_name


def tailor_job(job, master, master_txt):
    res = ai.tailor(job, master_txt)
    resume, cover, warns = ai.ground(res, master, master_txt)
    out = ROOT / "data" / "resumes"
    out.mkdir(parents=True, exist_ok=True)
    pdf = build_pdf(resume, master["contact"], out / f"{safe_name(master['contact']['name'], job['company'], job['title'])}.pdf")
    db.update(job["id"], status="queued", resume_json=json.dumps(resume), cover=cover, pdf_path=str(pdf),
              warnings=json.dumps(warns))


def run_fetch():
    master, master_txt = load_master()
    found = 0
    for j in sources.fetch_all():
        db.insert_job(j)
        found += 1
    print(f"Matched {found} jobs from boards (already-seen jobs are ignored).")
    for job in db.by_status("new"):
        r = ai.score(job, master_txt)
        ok = r["score"] >= cfg["min_score"]
        db.update(job["id"], status="scored" if ok else "skipped", score=r["score"], score_reason=r["reason"])
    run_tailor(master, master_txt)


def run_tailor(master=None, master_txt=None):
    if master is None:
        master, master_txt = load_master()
    todo = db.by_status("scored", "score DESC")[: cfg["max_tailor_per_run"]]
    for job in todo:
        print(f"Tailoring: {job['company']} - {job['title']} ({job['score']})")
        try:
            tailor_job(job, master, master_txt)
        except Exception as e:
            db.update(job["id"], status="error", notes=str(e))
            print(f"  ! {e}")
    print(f"Queued {len(todo)}. Run: python main.py review")
