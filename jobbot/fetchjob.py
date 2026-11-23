"""Reads the job description from a link. Login walls and bot checks are never bypassed: paste the text instead."""
import html
import re
from urllib.parse import urlparse

import httpx

NEED_PASTE = ("I couldn't read the job description from that link (it may need a login). "
              "Paste the description into the box and try again.")


def _clean(raw):
    t = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html.unescape(raw or ""))
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def _get(url):
    r = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r


def _from_ats(host, parts):
    """Greenhouse and Lever publish clean job data."""
    if "greenhouse.io" in host and len(parts) >= 3 and parts[1] == "jobs":
        j = _get(f"https://boards-api.greenhouse.io/v1/boards/{parts[0]}/jobs/{parts[2]}").json()
        return f"{j['title']} at {parts[0]}. Location: {(j.get('location') or {}).get('name', '')}. {_clean(j.get('content'))}"
    if host == "jobs.lever.co" and len(parts) >= 2:
        j = _get(f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}").json()
        lists = " ".join(f"{x.get('text', '')}: {_clean(x.get('content'))}" for x in j.get("lists", []))
        return f"{j['text']} at {parts[0]}. {j.get('descriptionPlain', '')} {lists}"
    return ""


def _rendered(url):
    """For pages that only show the job after JavaScript runs. A clean browser with no saved logins."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            page = b.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
            return re.sub(r"\s+", " ", page.inner_text("body")).strip()
        finally:
            b.close()


def read(url, pasted=""):
    pasted = (pasted or "").strip()
    if len(pasted) >= 200:
        return pasted
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("Paste a full job link starting with https://, or paste the job description.")
    text = ""
    try:
        text = _from_ats(u.netloc.lower(), [p for p in u.path.split("/") if p])
        if len(text) < 800:
            text = _clean(_get(url.strip()).text)
    except Exception:
        text = ""
    if len(text) < 800:
        try:
            text = _rendered(url.strip())
        except Exception:
            pass
    if len(text) < 300:
        raise ValueError(NEED_PASTE)
    return text
