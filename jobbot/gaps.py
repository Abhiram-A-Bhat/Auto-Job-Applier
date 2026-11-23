"""Information a tailored resume needs but you haven't given yet: ask once, remember it, update this job's resume."""
import json
import re
import threading
from pathlib import Path

from . import ai, db, memory
from .config import load_master
from .pdfgen import build_pdf

FACT = "resume_fact"
SKIP = "\x00skip"
NO = {"", "no", "nope", "none", "nil", "na", "n/a", "not applicable", "-", SKIP}
_lock = threading.RLock()


def is_no(answer):
    return str(answer or "").strip().lower() in NO


# ---- what you've already told us ------------------------------------------------------------------------------
def _facts():
    return [e for e in memory.load().values() if e.get("kind") == FACT]


def with_facts(master, master_txt):
    """Your remembered answers count as resume facts for tailoring and for the guardrails."""
    m = {**master, "skills": list(master.get("skills", [])), "certifications": list(master.get("certifications", []))}
    lines = []
    for e in _facts():
        ans, kind, name = str(e.get("answer", "")).strip(), e.get("fact"), e.get("name", "")
        if is_no(ans):
            continue
        if kind == "skill":
            if name.lower() not in {s.lower() for s in m["skills"]}:
                m["skills"].append(name)
            lines.append(f"- Experience with {name}: {ans}")
        elif kind == "cert":
            if name.lower() not in {c["name"].lower() for c in m["certifications"]}:
                m["certifications"].append({"name": name, "issuer": ans, "date": "", "about": ""})
            lines.append(f"- Certification: {name}, {ans}")
        elif kind == "number":
            lines.append("- " + e.get("template", "").replace("\x01", ans))
    if lines:
        master_txt += "\n\n# Facts the candidate confirmed later (same authority as the resume)\n" + "\n".join(lines)
    return m, master_txt


def still_open(found):
    """Drop anything you've already said no to."""
    declined = {(e.get("fact"), e.get("name", "").lower()) for e in _facts() if is_no(e.get("answer"))}
    return [g for g in found if (g["type"], g.get("name", "").lower()) not in declined]


# ---- editing the generated text --------------------------------------------------------------------------------
def _token(value):
    body = ",?".join(re.escape(c) for c in value)
    return re.compile(rf"(?<![\d.,]){body}(?!\d)(?!\.\d)(%|\+)?")


def _setter(value, answer):
    keep_suffix = bool(re.fullmatch(r"[\d,.]+", answer))  # "35" replacing "40%" stays "35%"
    return lambda t: _token(value).sub(lambda m: answer + ((m[1] or "") if keep_suffix else ""), t)


def _dropper(value):
    tok = _token(value)

    def drop(t):
        paras = re.split(r"\n\s*\n", t)
        kept = [" ".join(s for s in re.split(r"(?<=[.!?])\s+", p) if not tok.search(s)) for p in paras]
        return "\n\n".join(p for p in kept if p.strip())
    return drop


def _edit(resume, cover, fn):
    resume["summary"] = fn(resume.get("summary", ""))
    for group in ("experience", "projects"):
        for item in resume.get(group, []):
            item["bullets"] = [b for b in (fn(x) for x in item["bullets"]) if b.strip()]
    return fn(cover or "")


def _context(value, resume, cover):
    tok = _token(value)
    texts = [resume.get("summary", "")] + [b for g in ("experience", "projects") for i in resume.get(g, [])
                                           for b in i["bullets"]] + [cover or ""]
    for t in texts:
        for s in re.split(r"(?<=[.!?])\s+", t):
            if tok.search(s):
                return " ".join(s.split())[:220]
    return ""


# ---- asking and applying ----------------------------------------------------------------------------------------
def _label(g):
    if g["type"] == "skill":
        return f"This job mentions {g['name']}. Where have you used it? (type No if you haven't)"
    if g["type"] == "cert":
        return f'Do you hold the "{g["name"]}" certification? Type the issuer and date, or No.'
    return f'What is the real figure for {g["value"]} in: "{g["context"]}"? Type just the number, or Leave blank to remove it.'


def ask(job_id, found, resume, cover):
    db.close_gaps(job_id)
    for g in found:
        if g["type"] == "number":
            g = {**g, "context": _context(g["value"], resume, cover)}
        db.add_question(job_id, _label(g), FACT, "[]", "", gap=json.dumps(g))


def _valid_number(ans):
    return len(ans) <= 24 and bool(re.search(r"\d", ans))


def _apply(q):
    job = db.get(q["job_id"])
    if not job or job["status"] != "needs_info":
        return
    g, ans = json.loads(q["gap"]), str(q["answer"] or "").strip()
    no = is_no(ans)
    resume, cover = json.loads(job["resume_json"]), job["cover"] or ""
    if g["type"] == "number":
        if no:
            cover = _edit(resume, cover, _dropper(g["value"]))
        elif not _valid_number(ans):
            hint = "Please type only the number. "
            db.add_question(job["id"], q["label"] if q["label"].startswith(hint) else hint + q["label"],
                            FACT, "[]", "", gap=q["gap"])
            return
        else:
            keep = bool(re.fullmatch(r"[\d,.]+", ans))
            template = _token(g["value"]).sub(lambda m: "\x01" + ((m[1] or "") if keep else ""), g.get("context", ""))
            cover = _edit(resume, cover, _setter(g["value"], ans))
            if q["remember"]:
                memory.remember("Confirmed: " + template.replace("\x01", ans), ans, FACT, fact="number", template=template)
    else:
        if q["remember"] and ans != SKIP:
            memory.remember(q["label"], ans, FACT, fact=g["type"], name=g["name"])
        if not no and g["type"] == "skill" and g["name"].lower() not in {s.lower() for s in resume["skills"]}:
            resume["skills"].append(g["name"])
        if not no and g["type"] == "cert":
            resume.setdefault("certifications", []).append({"name": g["name"], "issuer": ans, "date": "", "about": ""})
    db.update(job["id"], resume_json=json.dumps(resume), cover=cover)
    if not db.open_gap_questions(job_id=job["id"]):
        try:
            build_pdf(resume, load_master()[0]["contact"], Path(job["pdf_path"]))
            db.update(job["id"], status="queued")
        except Exception as e:
            db.update(job["id"], status="error", notes=f"Couldn't rebuild the resume: {str(e)[:200]}")


def on_answer(qid):
    """Called whenever a question is answered (laptop page or phone). Form-field questions are ignored here."""
    with _lock:
        q = db.get_question(qid)
        if not q.get("gap"):
            return
        same = db.open_gap_questions(label=q["label"])  # one answer covers every job waiting on the same question
        for s in same:
            db.answer_question(s["id"], q["answer"], bool(q["remember"]))
        for x in [q] + same:
            _apply(db.get_question(x["id"]))


def migrate():
    """Jobs tailored before this feature: clear old warnings, ask about anything that is really missing."""
    master, txt = load_master()
    master, txt = with_facts(master, txt)
    for job in db.by_status("queued"):
        warns = json.loads(job["warnings"] or "[]")
        old = [w for w in warns if w.startswith("NUMBERS NOT IN MASTER")]
        if not old:
            continue
        resume, cover = json.loads(job["resume_json"]), job["cover"] or ""
        found = [{"type": "number", "value": n} for n in ai.number_gaps(resume, cover, txt, job)]
        db.update(job["id"], warnings=json.dumps([w for w in warns if w not in old]),
                  status="needs_info" if found else "queued")
        if found:
            ask(job["id"], found, resume, cover)
