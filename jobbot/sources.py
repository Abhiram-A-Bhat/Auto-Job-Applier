"""Job discovery via the ATS vendors' PUBLIC job-board APIs (no scraping, no login)."""
import html
import re

import httpx

from .config import cfg


def _clean(t):
    t = html.unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _get(url, **params):
    r = httpx.get(url, params=params, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.json()


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


def _wanted(j):
    s = cfg["search"]
    t = j["title"].lower()
    if s["include_titles"] and not any(k in t for k in s["include_titles"]):
        return False
    if any(k in t for k in s["exclude_titles"]):
        return False
    locs = s["locations"]
    return not locs or any(k in j["location"].lower() for k in locs)


def fetch_all():
    for kind, fn in (("greenhouse", greenhouse), ("lever", lever), ("ashby", ashby)):
        for name in cfg["sources"].get(kind) or []:
            try:
                for j in fn(name):
                    if _wanted(j):
                        yield j
            except Exception as e:  # bad board name, network, etc. - keep going
                print(f"  ! {kind}/{name}: {e}")
