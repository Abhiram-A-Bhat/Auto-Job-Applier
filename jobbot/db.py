import sqlite3
import time

from .config import ROOT

DB = ROOT / "data" / "jobs.db"
# status: new -> scored | skipped -> queued -> approved -> submitted
#         queued -> rejected | applied_manual ; approved -> needs_manual | error


def conn():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS jobs(
        id TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, location TEXT,
        url TEXT, apply_url TEXT, description TEXT, status TEXT DEFAULT 'new',
        score INTEGER, score_reason TEXT, resume_json TEXT, cover TEXT, pdf_path TEXT,
        warnings TEXT, notes TEXT, created REAL, updated REAL)"""
    )
    try:
        c.execute("ALTER TABLE jobs ADD COLUMN salary_lpa REAL")  # migration for older databases
    except sqlite3.OperationalError:
        pass
    c.execute(
        """CREATE TABLE IF NOT EXISTS questions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, label TEXT, kind TEXT, options TEXT,
        suggestion TEXT, answer TEXT, remember INTEGER DEFAULT 1, created REAL)"""
    )
    try:
        c.execute("ALTER TABLE questions ADD COLUMN gap TEXT")  # set when the question fills a gap in the resume
    except sqlite3.OperationalError:
        pass
    return c


def add_question(job_id, label, kind, options, suggestion, gap=None):
    with conn() as c:
        return c.execute("INSERT INTO questions(job_id,label,kind,options,suggestion,created,gap) VALUES(?,?,?,?,?,?,?)",
                         (job_id, label, kind, options, suggestion, time.time(), gap)).lastrowid


def open_gap_questions(label=None, job_id=None):
    field, value = ("label", label) if label is not None else ("job_id", job_id)
    with conn() as c:
        return [dict(r) for r in c.execute(
            f"SELECT * FROM questions WHERE gap IS NOT NULL AND answer IS NULL AND {field}=?", (value,))]


def close_gaps(job_id):
    with conn() as c:
        c.execute("UPDATE questions SET answer=?, remember=0 WHERE job_id=? AND gap IS NOT NULL AND answer IS NULL",
                  ("\x00skip", job_id))


def get_question(qid):
    with conn() as c:
        return dict(c.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone())


def pending_questions():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT q.*, j.company, j.title FROM questions q JOIN jobs j ON j.id=q.job_id "
            "WHERE q.answer IS NULL AND (q.gap IS NULL OR q.id=(SELECT MIN(x.id) FROM questions x "
            "WHERE x.label=q.label AND x.gap IS NOT NULL AND x.answer IS NULL)) ORDER BY q.id")]


def answer_question(qid, answer, remember=True):
    with conn() as c:
        c.execute("UPDATE questions SET answer=?, remember=? WHERE id=?", (answer, int(remember), qid))


def insert_job(j):
    with conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO jobs(id,source,company,title,location,url,apply_url,
            description,status,created,updated,salary_lpa) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (j["id"], j["source"], j["company"], j["title"], j.get("location", ""), j["url"],
             j.get("apply_url") or j["url"], j["description"], j.get("status", "new"),
             time.time(), time.time(), j.get("salary_lpa")),
        )


def by_status(status, order="created DESC"):
    with conn() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM jobs WHERE status=? ORDER BY {order}", (status,))]


def get(job_id):
    with conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None


def update(job_id, **fields):
    fields["updated"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def by_statuses(statuses):
    with conn() as c:
        q = f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY created"
        return [dict(r) for r in c.execute(q, tuple(statuses))]


def known_urls(source):
    with conn() as c:
        return {r[0] for r in c.execute("SELECT url FROM jobs WHERE source=?", (source,))}


def counts():
    with conn() as c:
        return {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
