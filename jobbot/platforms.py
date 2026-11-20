"""LinkedIn / Naukri / Indeed / Wellfound, driven in YOUR logged-in jobbot browser window.

- You log in yourself (button in the app); the tool never sees or stores your passwords.
- Visible browser, a few seconds between page loads, small per-search limits.
- Login walls and CAPTCHA / "verify you are human" checks are never bypassed: the tool pauses until you clear them.
- The final Submit (and Naukri's one-click Apply) is always yours.
These sites' terms forbid automation and they do restrict accounts - keep the limits in config.yaml modest.
"""
import hashlib
import re
from urllib.parse import quote_plus, urlparse

from .config import cfg

PLATFORMS = ("linkedin", "naukri", "indeed", "wellfound")
HOSTS = {"linkedin": "linkedin.com", "naukri": "naukri.com", "indeed": "indeed.com", "wellfound": "wellfound.com"}
LOGIN = {"linkedin": "https://www.linkedin.com/login", "naukri": "https://www.naukri.com/nlogin/login",
         "indeed": "https://secure.indeed.com/auth", "wellfound": "https://wellfound.com/login"}
DESC_SEL = {"linkedin": "#job-details, .jobs-description__content, .description__text",
            "indeed": "#jobDescriptionText", "naukri": "[class*='job-desc'], [class*='JDC']",
            "wellfound": "[class*='description']"}
APPLY = {"linkedin": r"^(easy apply|apply)", "indeed": r"^(apply now|apply on company site|apply)",
         "wellfound": r"^apply", "naukri": r"^apply"}
NEXT = re.compile(r"^(next|continue|review|review your application|continue to next step|save and continue)$", re.I)
SUBMIT = re.compile(r"^(submit application|submit|send application|submit your application)$", re.I)
BLOCK_URL = ("checkpoint", "challenge", "captcha", "/login", "signin", "authwall", "nlogin", "/uas/", "/auth")


def enabled():
    return set((cfg.get("platforms") or {}).get("enabled") or [])


def platform_of(url):
    h = urlparse(url).netloc.lower()
    return next((n for n, d in HOSTS.items() if h == d or h.endswith("." + d)), None)


def job_id(name, url):
    return f"{name}:" + hashlib.md5(url.encode()).hexdigest()[:12]


def _slug(q):
    return re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")


def search_urls(name):
    """Two searches per query: Bengaluru, and remote."""
    p = cfg["platforms"]
    out = []
    for q in p.get("queries", ["software engineer"]):
        k, s = quote_plus(q), _slug(q)
        if name == "linkedin":
            ea = "&f_AL=true" if p.get("linkedin_easy_apply_only", True) else ""
            out += [f"https://www.linkedin.com/jobs/search/?keywords={k}&location=Bengaluru%2C%20Karnataka%2C%20India&f_TPR=r604800{ea}",
                    f"https://www.linkedin.com/jobs/search/?keywords={k}&location=India&f_WT=2&f_TPR=r604800{ea}"]
        elif name == "naukri":
            out += [f"https://www.naukri.com/{s}-jobs-in-bangalore?jobAge=7", f"https://www.naukri.com/{s}-jobs?wfhType=2&jobAge=7"]
        elif name == "indeed":
            out += [f"https://in.indeed.com/jobs?q={k}&l=Bengaluru%2C+Karnataka&fromage=7",
                    f"https://in.indeed.com/jobs?q={k}&l=Remote&fromage=7"]
        elif name == "wellfound":
            out += [f"https://wellfound.com/role/l/{s}/bangalore", f"https://wellfound.com/role/r/{s}"]
    return out


def canonical(name, href):
    m = None
    if name == "linkedin":
        m = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d{6,})", href) or \
            re.search(r"linkedin\.com/jobs/.*[?&]currentJobId=(\d+)", href)
        return f"https://www.linkedin.com/jobs/view/{m[1]}/" if m else None
    if name == "indeed":
        m = re.search(r"indeed\.com/.*[?&]jk=([0-9a-f]{8,})", href)
        return f"https://in.indeed.com/viewjob?jk={m[1]}" if m else None
    if name == "naukri":
        m = re.search(r"https://www\.naukri\.com/job-listings-[^?#]+", href)
    if name == "wellfound":
        m = re.search(r"https://wellfound\.com/jobs/\d+[^?#]*", href)
    return m[0] if m else None


# ---- login walls / human checks: never bypassed, we wait for you --------------------------------------------
def blocked(page):
    try:
        u, t = page.url.lower(), page.title().lower()
        if any(k in u for k in BLOCK_URL) or "just a moment" in t or ("verify" in t and "human" in t):
            return True
        return page.locator("iframe[src*='captcha'], iframe[src*='challenge'], #challenge-form").count() > 0
    except Exception:
        return False


def wait(page, ms):
    """page.wait_for_timeout that still handles button clicks (e.g. 'Open jobbot browser') while it waits."""
    from . import formfill
    from .pipeline import check_stop
    end = ms
    while end > 0:
        check_stop()  # Stop button
        try:
            page.wait_for_timeout(min(end, 1000))
        except Exception:
            if page.is_closed():
                return  # you closed the tab: the caller decides what that means
            raise
        end -= 1000
        formfill.pump(page.context)


def _typing_password(page):
    try:
        return page.locator("input[type=password]:visible").count() > 0
    except Exception:
        return False


def _settled(page, checks=3):
    """True once the page has stayed off login/verification screens for `checks` looks in a row (~3s apart).
    Logins hop through several pages (password -> 'verify it's you' -> OTP -> redirect); a single clear look
    mid-way is not the end, so we never move on (or reload) until it has really settled."""
    for _ in range(checks):
        if page.is_closed() or blocked(page) or _typing_password(page):
            return False
        wait(page, 3000)
    return not (page.is_closed() or blocked(page) or _typing_password(page))


def wait_until_clear(page, notify, minutes=15):
    if not blocked(page):
        return
    notify("A login or verification check is showing in the jobbot browser window. Complete it there yourself; "
           "I'll wait and continue automatically (nothing reloads while you do it).")
    try:
        page.bring_to_front()
    except Exception:
        pass
    for _ in range(minutes * 20):
        wait(page, 3000)
        if page.is_closed():
            raise RuntimeError("Verification tab was closed - skipped")
        if not blocked(page) and _settled(page):
            return
    raise RuntimeError("Timed out waiting for you to log in / pass the verification check")


# Cookies these sites set only after a completed sign-in. Unknown/changed names just fall back to watching the page.
AUTH_COOKIES = {"linkedin": ("li_at",), "naukri": ("nauk_at",), "indeed": ("PPID", "SHOE"), "wellfound": ()}


def has_login(context, name):
    try:
        return any(c["name"] in AUTH_COOKIES.get(name, ()) and HOSTS[name] in c["domain"]
                   for c in context.cookies())
    except Exception:
        return False


def ensure_logged_in(browser, name, notify):
    """Before searching a platform: if you're not signed in, open its login page and wait - without touching the
    page - until you've finished (including OTP / 'verify it's you' steps). Close the tab to skip that platform."""
    if has_login(browser, name):
        return
    minutes = (cfg.get("platforms") or {}).get("login_wait_minutes", 30)
    page = browser.new_page()
    page.goto(LOGIN[name], wait_until="domcontentloaded", timeout=45000)
    wait(page, 3000)
    if not AUTH_COOKIES.get(name) and not blocked(page) and not _typing_password(page):
        page.close()  # login page bounced us away: already signed in
        return
    try:
        page.bring_to_front()
    except Exception:
        pass
    notify(f"{name}: please log in in the jobbot browser window (the {name} tab). I'll wait up to {minutes} min "
           f"and continue on my own when you're done - close that tab to skip {name}.")
    for _ in range(minutes * 20):
        if page.is_closed():
            raise RuntimeError(f"login tab closed - skipped {name}")
        if has_login(browser, name) and not blocked(page) and not _typing_password(page):
            wait(page, 3000)  # let the post-login redirect finish
            break
        if not AUTH_COOKIES.get(name) and _settled(page):
            break
        wait(page, 3000)
    else:
        raise RuntimeError(f"not logged in to {name} after {minutes} min - skipped this time")
    notify(f"{name}: logged in, searching...")
    if not page.is_closed():
        page.close()


# ---- reading ------------------------------------------------------------------------------------------------
def read_page(page, url, notify):
    name = platform_of(url)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3000)
    if blocked(page):
        wait_until_clear(page, notify)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
    h1 = page.locator("h1").first
    title = h1.inner_text(timeout=3000).strip() if h1.count() else ""
    desc = ""
    try:
        d = page.locator(DESC_SEL.get(name, "main")).first
        desc = d.inner_text(timeout=3000) if d.count() else ""
    except Exception:
        pass
    main = page.locator("main").first
    head = (main.inner_text(timeout=3000) if main.count() else page.inner_text("body"))[:1500]
    text = desc if len(desc) > 300 else page.inner_text("body")[:15000]
    return {"url": url, "title": title, "page_title": page.title(), "text": (head + "\n\n" + text)[:15000]}


def read_new_page(browser, url, notify):
    page = browser.new_page()
    try:
        return read_page(page, url, notify)
    finally:
        page.close()


def to_job(name, r):
    title, company, location = r["title"], "Unknown", ""
    m = re.match(r"^(.*?) hiring (.*?) in (.*?) \| LinkedIn", r.get("page_title", ""))
    if m:
        company, title, location = m[1], m[2], m[3]
    return dict(id=job_id(name, r["url"]), source=name, company=company, title=title or r["page_title"][:80],
                location=location, url=r["url"], apply_url=r["url"], description=r["text"])


def discover(browser, name, notify, known):
    """Collect up to max_jobs_per_platform NEW job pages from this platform's search results."""
    p = cfg["platforms"]
    limit, delay = p.get("max_jobs_per_platform", 15), p.get("delay_seconds", 6) * 1000
    ensure_logged_in(browser, name, notify)
    page = browser.new_page()
    urls = []
    try:
        for su in search_urls(name):
            notify(f"{name}: searching...")
            page.goto(su, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            if blocked(page):
                wait_until_clear(page, notify, minutes=5)  # unattended search: don't hold the browser for long
                page.goto(su, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)
            for _ in range(4):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(900)
            for h in page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => a.href)"):
                c = canonical(name, h)
                if c and c not in urls and c not in known:
                    urls.append(c)
            if len(urls) >= limit:
                break
            wait(page, delay)
        out = []
        for n, u in enumerate(urls[:limit], 1):
            notify(f"{name}: reading job {n}/{min(limit, len(urls))}")
            try:
                out.append(read_page(page, u, notify))
            except Exception as e:
                notify(f"{name}: skipped a job ({str(e)[:60]})")
            wait(page, delay)
        return out
    finally:
        page.close()


# ---- applying -----------------------------------------------------------------------------------------------
def open_application(page, job, notify):
    """Open the job and click into its application. Returns (page, note) - the page may be a new tab."""
    name = job["source"]
    page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3000)
    if blocked(page):
        wait_until_clear(page, notify)
        page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
    if name == "naukri":
        return page, ("Naukri's Apply button submits instantly with your Naukri profile, so press it yourself "
                      "(then answer any questions it shows).")
    rx = re.compile(APPLY[name], re.I)
    tabs = len(page.context.pages)
    page.get_by_role("button", name=rx).or_(page.get_by_role("link", name=rx)).first.click(timeout=8000)
    page.wait_for_timeout(3500)
    if len(page.context.pages) > tabs:  # "Apply on company site" opens a new tab
        page = page.context.pages[-1]
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)
    wait_until_clear(page, notify)
    return page, None


def step_through(page, fill):
    """Multi-step forms (LinkedIn Easy Apply, Indeed): fill, Next, fill... stop at the final Submit."""
    seen = 0
    for _ in range(12):
        seen += fill()
        if page.get_by_role("button", name=SUBMIT).count():
            break
        nxt = page.get_by_role("button", name=NEXT)
        if not nxt.count():
            break
        nxt.first.click(timeout=5000)
        page.wait_for_timeout(2000)
    return seen
