"""Job discovery from sources that allow it: public ATS APIs, Adzuna (India, free key) and Remotive.
LinkedIn / Indeed / Naukri / Wellfound are deliberately NOT scraped (against their terms, bot-blocked)."""
import html
import os
import re

import httpx

from .config import cfg


def _clean(t):
    t = html.unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _get(url, **params):
    r = httpx.get(url, params=params, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()


# ---- salary: annual, in lakhs of INR (LPA) ---------------------------------------------------------------
FX = {"$": 85.0, "€": 95.0, "£": 110.0}  # rough INR rates, only used to compare with the threshold


def salary_lpa(text, loose=False):
    """Best (highest) annual salary found in text, in LPA, or None. loose=True also reads $/EUR/GBP amounts
    (only used on dedicated salary fields, since descriptions mention dollars for unrelated reasons)."""
    t = (text or "").lower().replace(",", "")
    vals = []
    vals += [float(m[1]) for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:lpa|lakhs?|lacs?)", t)]
    vals += [float(m[1]) for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:lpa|lakhs?|lacs?)", t)]
    for m in re.finditer(r"(?:₹|rs\.?|inr)\s*(\d{5,9})(?:\s*(?:-|–|to)\s*(?:₹|rs\.?|inr)?\s*(\d{5,9}))?", t):
        v = float(m[2] or m[1])
        if re.search(r"month|/m\b|pm\b", t[m.end(): m.end() + 30]):
            v *= 12
        vals.append(v / 1e5)
    if loose:
        for m in re.finditer(r"([$€£])\s?(\d+(?:\.\d+)?)\s?(k)?(?:\s*(?:-|–|to)\s*[$€£]?\s?(\d+(?:\.\d+)?)\s?(k)?)?", t):
            if re.search(r"hour|/hr|per hr", t[m.end(): m.end() + 15]):
                continue
            hi = float(m[4] or m[2]) * (1000 if (m[5] if m[4] else m[3]) else 1)
            if hi >= 1000:
                vals.append(hi * FX[m[1]] / 1e5)
    return max(vals) if vals else None


def salary_ok(j):
    s = cfg["search"]
    lpa = j.get("salary_lpa")
    if lpa is None:
        lpa = j["salary_lpa"] = salary_lpa(j["description"])
    if lpa is None:
        return s.get("unknown_salary", "include") == "include"
    return lpa >= s.get("min_salary_lpa", 6)


# ---- sources ---------------------------------------------------------------------------------------------
def greenhouse(token):
    for j in _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", content="true")["jobs"]:
        yield dict(id=f"greenhouse:{token}:{j['id']}", source="greenhouse", company=token,
                   title=j["title"], location=(j.get("location") or {}).get("name", ""),
                   url=j["absolute_url"], apply_url=j["absolute_url"], description=_clean(j.get("content")))


def lever(slug):
    for j in _get(f"https://api.lever.co/v0/postings/{slug}", mode="json"):
        yield dict(id=f"lever:{slug}:{j['id']}", source="lever", company=slug, title=j["text"],
                   location=(j.get("categories") or {}).get("location", ""), url=j["hostedUrl"],
                   apply_url=j.get("applyUrl") or j["hostedUrl"], description=j.get("descriptionPlain", ""))


def ashby(name):
    for j in _get(f"https://api.ashbyhq.com/posting-api/job-board/{name}")["jobs"]:
        yield dict(id=f"ashby:{name}:{j['id']}", source="ashby", company=name, title=j["title"],
                   location=j.get("location", "") or ("Remote" if j.get("isRemote") else ""),
                   url=j["jobUrl"], apply_url=j.get("applyUrl") or j["jobUrl"],
                   description=j.get("descriptionPlain", ""))


def adzuna(query):
    """India-wide aggregator with an official API. Free key: developer.adzuna.com"""
    aid, key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (aid and key):
        raise RuntimeError("Adzuna skipped: set ADZUNA_APP_ID and ADZUNA_APP_KEY (free at developer.adzuna.com)")
    s = cfg["search"]
    params = dict(app_id=aid, app_key=key, what=query, results_per_page=50, sort_by="date",
                  max_days_old=s.get("max_days_old", 7))
    if s.get("adzuna_where"):
        params["where"] = s["adzuna_where"]
    for j in _get("https://api.adzuna.com/v1/api/jobs/in/search/1", **params)["results"]:
        amount = j.get("salary_max") or j.get("salary_min")
        known = amount and str(j.get("salary_is_predicted")) != "1"  # ignore Adzuna's guessed salaries
        yield dict(id=f"adzuna:{j['id']}", source="adzuna", company=(j.get("company") or {}).get("display_name", "Unknown"),
                   title=_clean(j["title"]), location=(j.get("location") or {}).get("display_name", ""),
                   url=j["redirect_url"], apply_url=j["redirect_url"], description=_clean(j.get("description")),
                   salary_lpa=amount / 1e5 if known else None)


def remotive():
    """Remote software jobs; kept only if open to India/worldwide."""
    for j in _get("https://remotive.com/api/remote-jobs", category="software-dev", limit=100)["jobs"]:
        loc = (j.get("candidate_required_location") or "").lower()
        if loc and not any(k in loc for k in ("worldwide", "anywhere", "india", "asia", "apac")):
            continue
        yield dict(id=f"remotive:{j['id']}", source="remotive", company=j["company_name"], title=j["title"],
                   location="Remote - " + (j.get("candidate_required_location") or "Worldwide"), url=j["url"],
                   apply_url=j["url"], description=_clean(j.get("description")),
                   salary_lpa=salary_lpa(j.get("salary") or "", loose=True))


# ---- filtering + orchestration ---------------------------------------------------------------------------
def _wanted(j, check_loc=True):
    s = cfg["search"]
    t = j["title"].lower()
    if s["include_titles"] and not any(k in t for k in s["include_titles"]):
        return False
    if any(k in t for k in s["exclude_titles"]):
        return False
    loc = j["location"].lower()
    if check_loc and s["locations"] and not any(k in loc for k in s["locations"]):
        return False
    # "Remote - US", "Remote, EMEA"... are remote only inside another region: drop them for free here
    if re.search(FOREIGN, loc) and not re.search(r"india|apac|asia|worldwide|anywhere|global", loc):
        return False
    return True


FOREIGN = (r"\b(us|usa|united states|uk|united kingdom|emea|europe|eu|canada|ireland|germany|france|spain|"
           r"netherlands|poland|americas|amer|latam|north america|brazil|mexico|australia|japan|singapore|israel|"
           r"san francisco|new york|seattle|london|toronto|dublin|berlin)\b")


def _safe(label, gen):
    try:
        yield from gen
    except Exception as e:  # bad board name, missing key, network... keep going
        print(f"  ! {label}: {e}")


def fetch_all():
    for kind, fn in (("greenhouse", greenhouse), ("lever", lever), ("ashby", ashby)):
        for name in cfg["sources"].get(kind) or []:
            for j in _safe(f"{kind}/{name}", fn(name)):
                if _wanted(j) and salary_ok(j):
                    yield j
    if cfg["sources"].get("adzuna"):
        for q in cfg["search"].get("queries", ["software engineer"]):
            for j in _safe(f"adzuna/{q}", adzuna(q)):
                if _wanted(j) and salary_ok(j):
                    yield j
    if cfg["sources"].get("remotive"):
        for j in _safe("remotive", remotive()):
            if _wanted(j, check_loc=False) and salary_ok(j):
                yield j
