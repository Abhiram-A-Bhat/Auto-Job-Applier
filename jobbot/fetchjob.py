"""Turn a pasted job link into a job dict. ATS links use public APIs; other pages are read as text.
LinkedIn / Indeed / Wellfound etc. are never fetched or automated: you paste the description yourself."""
import hashlib
import html
import re
from urllib.parse import urlparse

import httpx

from . import ai, sources

MANUAL_HOSTS = ("linkedin.com", "indeed.com", "wellfound.com", "angel.co", "talboss")


def is_manual(url):
    host = urlparse(url).netloc.lower()
    return any(h in host for h in MANUAL_HOSTS)


def _get(url):
    r = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r


def _page_text(url):
    t = _get(url).text
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def _gh(token, jid, url):
    j = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}").json()
    return dict(id=f"greenhouse:{token}:{jid}", source="greenhouse", company=token.title(), title=j["title"],
                location=(j.get("location") or {}).get("name", ""), url=url, apply_url=url,
                description=sources._clean(j.get("content")))


def _lever(slug, jid, url):
    j = _get(f"https://api.lever.co/v0/postings/{slug}/{jid}").json()
    return dict(id=f"lever:{slug}:{jid}", source="lever", company=slug.title(), title=j["text"],
                location=(j.get("categories") or {}).get("location", ""), url=url,
                apply_url=j.get("applyUrl") or url, description=j.get("descriptionPlain", ""))


def _ashby(name, jid, url):
    for j in sources.ashby(name):
        if j["id"].endswith(jid):
            return {**j, "company": name.title(), "url": url}
    raise ValueError("job not found on Ashby board")


def resolve(url, pasted=""):
    url = url.strip()
    pasted = (pasted or "").strip()
    u = urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    uid = hashlib.md5(url.split("?")[0].encode()).hexdigest()[:12]
    if is_manual(url):
        if len(pasted) < 200:
            raise ValueError("For LinkedIn/Indeed/Wellfound/Talboss I don't fetch or automate the site. "
                             "Paste the job description in the box and I'll tailor your resume; you apply yourself.")
        info = ai.extract_job(pasted)
        return dict(id=f"manual:{uid}", source="manual", company=info.get("company") or "Unknown",
                    title=info.get("title") or "Role", location=info.get("location", ""), url=url, apply_url=url,
                    description=info.get("description") or pasted)
    try:
        if "greenhouse.io" in host and len(parts) >= 3 and parts[1] == "jobs":
            return _gh(parts[0], parts[2], url)
        if host == "jobs.lever.co" and len(parts) >= 2:
            return _lever(parts[0], parts[1], url)
        if host == "jobs.ashbyhq.com" and len(parts) >= 2:
            return _ashby(parts[0], parts[1], url)
    except Exception:
        pass  # fall through to generic reading
    text = pasted or _page_text(url)
    if len(text) < 300:
        raise ValueError("I couldn't read a job description from that link (it may need JavaScript or a login). "
                         "Paste the description into the box and try again.")
    info = ai.extract_job(text)
    return dict(id=f"web:{uid}", source="web", company=info.get("company") or host, title=info.get("title") or "Role",
                location=info.get("location", ""), url=url, apply_url=url, description=info.get("description") or text)
