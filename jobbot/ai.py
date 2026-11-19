import json
import re

import anthropic

from .config import cfg

_client = None


def _call(model, system, user, max_tokens):
    global _client
    _client = _client or anthropic.Anthropic()
    r = _client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                messages=[{"role": "user", "content": user}])
    text = re.sub(r"^```(?:json)?|```$", "", r.content[0].text.strip(), flags=re.M).strip()
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def _resume_block(master_txt):
    # Same text on every call -> prompt caching makes re-reads ~90% cheaper.
    return {"type": "text", "text": "CANDIDATE MASTER RESUME (only source of truth):\n" + master_txt,
            "cache_control": {"type": "ephemeral"}}


def score(job, master_txt):
    sys = [{"type": "text", "text": (
        "You judge how well a job fits a candidate, using ONLY the master resume. Be strict: 90+ = strong "
        "match on core requirements, 70-89 = good, <70 = stretch/mismatch (seniority, missing must-have "
        'skills, wrong domain). Reply with JSON only: {"score": int, "reason": "one sentence"}')},
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
