import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from .config import ROOT, cfg

_client = None
API_IDS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
USAGE_LOG = ROOT / "data" / "usage.jsonl"


def _claude_bin():
    """Claude Code executable: $CLAUDE_BIN, PATH, or the copy bundled with the Claude desktop app."""
    found = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not found:
        pat = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-Data", "*", "claude-code", "*", "*", "claude.exe")
        found = max(glob.glob(pat), default=None)
    if not found:
        raise RuntimeError("Claude Code CLI not found. Install it or set CLAUDE_BIN to claude.exe.")
    return found


def _log_usage(model, usage):
    USAGE_LOG.parent.mkdir(exist_ok=True)
    row = {"t": time.time(), "model": model, "input": usage.get("input_tokens", 0),
           "cache_read": usage.get("cache_read_input_tokens", 0),
           "cache_write": usage.get("cache_creation_input_tokens", 0), "output": usage.get("output_tokens", 0)}
    with USAGE_LOG.open("a") as f:
        f.write(json.dumps(row) + "\n")


def _text_via_cli(model, system, user):
    """Uses your logged-in Claude subscription (no API key). Tools/MCP/skills are off so it's a plain text call."""
    prompt = "\n\n".join(b["text"] for b in system) + "\n\n" + user + "\n\nReply with the JSON object only."
    cmd = [_claude_bin(), "-p", "--model", model, "--tools", "", "--system-prompt",
           "You are a JSON-only API. Output a single JSON object and nothing else.", "--output-format", "json",
           "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config"]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=300,
                       cwd=tempfile.gettempdir())
    out = json.loads(r.stdout)
    if out.get("is_error"):
        raise RuntimeError(out.get("result", "claude CLI error"))
    _log_usage(model, out.get("usage", {}))
    return out["result"]


def _text_via_api(model, system, user, max_tokens):
    global _client
    import anthropic
    _client = _client or anthropic.Anthropic()
    r = _client.messages.create(model=API_IDS.get(model, model), max_tokens=max_tokens, system=system,
                                messages=[{"role": "user", "content": user}])
    _log_usage(model, {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens,
                       "cache_read_input_tokens": getattr(r.usage, "cache_read_input_tokens", 0) or 0})
    return r.content[0].text


def _call(model, system, user, max_tokens):
    text = (_text_via_api(model, system, user, max_tokens) if cfg.get("backend") == "api"
            else _text_via_cli(model, system, user))
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def _resume_block(master_txt):
    # Same text on every call -> prompt caching makes re-reads ~90% cheaper.
    return {"type": "text", "text": "CANDIDATE MASTER RESUME (only source of truth):\n" + master_txt,
            "cache_control": {"type": "ephemeral"}}


def score(job, master_txt):
    sys = [{"type": "text", "text": (
        "You judge how well a job fits a candidate, using ONLY the master resume. Be strict: 90+ = strong "
        "match on core requirements, 70-89 = good, <70 = stretch/mismatch (seniority, missing must-have "
        'skills, wrong domain). The candidate lives in India and is a final-year student with about 3 months of '
        "internship experience: score under 40 for roles restricted to another country/region (e.g. 'Remote - US') "
        'or requiring 2+ years of experience. Reply with JSON only: {"score": int, "reason": "one sentence"}')},
        _resume_block(master_txt)]
    user = f"JOB: {job['title']} at {job['company']} ({job['location']})\n\n{job['description'][:6000]}"
    return _call(cfg["models"]["score"], sys, user, 200)


RULES = """You tailor a resume + cover letter for ONE job, using ONLY facts in the master resume.

HARD RULES
1. ATS-readable: plain text only. No tables, emoji, icons, symbols, ratings or skill bars. Use short plain bullets.
2. Keyword MAPPING, not stuffing: find the 3-6 core problems this employer needs solved, then use the job's
   own terminology only to describe work the candidate has ACTUALLY done. If a keyword can't be backed by a
   specific item in the master resume, leave it out. Never claim a skill/tool/employer/title/date not in the master.
3. Sound like the candidate: reuse their own phrasing, only tightening wording and clarity. Banned filler:
   "results-driven", "passionate", "dynamic", "synergy", "proven track record", "leverage", "spearheaded".
4. Impact with numbers: write bullets as Google XYZ ("Accomplished X, measured by Y, by doing Z") WHEN the
   master supplies the real numbers. NEVER invent or estimate a number, percentage or timeframe. If a bullet has
   no metric in the master, keep it plain and accurate.
5. AI skills must be shown, not listed: if the role values AI/automation and the master has a relevant project,
   include it under projects with HOW AI was used to solve a real problem. Never just list tool names.
6. Select and order: keep the most relevant 3-5 bullets per role, most relevant first; drop weak/irrelevant ones.
   Aim for one page. Summary: 2-3 plain sentences specific to this role.
7. Cover letter: under 170 words, specific to this company and role, no clichés, same voice, no invented facts.

Reply with JSON only:
{"summary": str, "skills": [str],
 "experience": [{"company": str, "title": str, "dates": str, "location": str, "bullets": [str]}],
 "projects": [{"name": str, "bullets": [str]}],
 "cover_letter": str,
 "keywords_mapped": [{"keyword": str, "evidence": "which resume item backs it"}]}"""


def tailor(job, master_txt):
    sys = [{"type": "text", "text": RULES}, _resume_block(master_txt)]
    user = f"JOB: {job['title']} at {job['company']} ({job['location']})\n\n{job['description'][:8000]}"
    return _call(cfg["models"]["tailor"], sys, user, 4000)


def _nums(t):
    return {n.rstrip(".,").replace(",", "") for n in re.findall(r"\d[\d,]*\.?\d*", t)}


def ground(res, master, master_txt):
    """Hard guardrail: force identity facts from the master, drop unbacked skills, flag unknown numbers."""
    warns = []
    mexp = {e["company"].lower(): e for e in master["experience"]}
    exp = []
    for e in res.get("experience", []):
        m = mexp.get(str(e.get("company", "")).lower())
        if not m:
            warns.append(f"Dropped unknown employer: {e.get('company')}")
            continue
        exp.append({**{k: m.get(k, "") for k in ("company", "title", "dates", "location")},
                    "bullets": e.get("bullets", [])})
    mskills = {s.lower() for s in master.get("skills", [])}
    skills = [s for s in res.get("skills", []) if s.lower() in mskills]
    if len(skills) < len(res.get("skills", [])):
        warns.append("Dropped skills not in master: " +
                     ", ".join(s for s in res["skills"] if s.lower() not in mskills))
    mproj = {p["name"].lower(): p for p in master.get("projects", [])}
    projs = [{"name": mproj[p["name"].lower()]["name"], "link": mproj[p["name"].lower()].get("link", ""),
              "bullets": p.get("bullets", [])} for p in res.get("projects", []) if p.get("name", "").lower() in mproj]
    text = " ".join([res.get("summary", ""), res.get("cover_letter", "")] +
                    [b for e in exp for b in e["bullets"]] + [b for p in projs for b in p["bullets"]])
    bad = _nums(text) - _nums(master_txt)
    if bad:
        warns.append("NUMBERS NOT IN MASTER RESUME (verify or reject): " + ", ".join(sorted(bad)))
    out = {"summary": res.get("summary", ""), "skills": skills, "experience": exp, "projects": projs,
           "education": master.get("education", []), "achievements": master.get("achievements", []),
           "keywords_mapped": res.get("keywords_mapped", [])}
    return out, res.get("cover_letter", ""), warns


def extract_job(text):
    sys = [{"type": "text", "text": (
        "Extract the job from raw page text. JSON only: "
        '{"title": str, "company": str, "location": str, "description": str (role, responsibilities and '
        "requirements, cleaned of navigation/footer text)}")}]
    return _call(cfg["models"]["score"], sys, text[:15000], 3000)


def draft_answer(job, master_txt, question):
    sys = [{"type": "text", "text": (
        "Draft a concise (60-120 words) answer to a job-application question in the candidate's own plain voice, "
        "using ONLY facts from the master resume. No cliches, no invented facts or numbers. If the resume gives "
        'no basis, return an empty string. JSON only: {"answer": str}')}, _resume_block(master_txt)]
    try:
        return _call(cfg["models"]["tailor"], sys,
                     f"JOB: {job['title']} at {job['company']}\n{job['description'][:3000]}\n\nQUESTION: {question}",
                     500).get("answer", "")
    except Exception:
        return ""


def kpi_ideas(master_txt):
    sys = [{"type": "text", "text": (
        "For each role in the resume, list 3-5 QUESTIONS the candidate can answer from their own records to "
        "find real, measurable impact (time saved, speed, accuracy, volume, revenue, cost, users). Do NOT "
        'invent numbers. JSON only: {"roles": [{"company": str, "questions": [str]}]}')},
        _resume_block(master_txt)]
    return _call(cfg["models"]["score"], sys, "Suggest KPI questions.", 1500)
