"""Memory-backed form filler. Scans any application form, fills what it knows (resume contact details +
answers.json), and ASKS you (in the web app) only for what it doesn't know, then remembers the answer."""
import json
import queue
import re
import threading

from playwright.sync_api import sync_playwright

from . import ai, db, memory
from .config import ROOT, cfg, load_master
from .pdfgen import build_cover_pdf, safe_name

SKIP = "\x00skip"
YES = {"yes", "y", "true", "1", "checked", "agree", "i agree"}

SCAN_JS = r"""() => {
  const vis = e => !!(e.offsetParent || e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
  const txt = e => ((e && (e.innerText || e.textContent)) || '').replace(/\s+/g, ' ').trim();
  const idOf = e => { let v = e.getAttribute('data-jb');
    if (!v) { window.__jbn = (window.__jbn || 0) + 1; v = 'f' + window.__jbn; e.setAttribute('data-jb', v); } return v; };
  const labelOf = (e, skip, noOwn) => {
    if (!noOwn) {
      if (e.labels && e.labels.length) { const t = txt(e.labels[0]); if (t) return t; }
      const al = e.getAttribute('aria-label'); if (al) return al;
      const lb = e.getAttribute('aria-labelledby');
      if (lb) { const t = lb.split(/\s+/).map(i => txt(document.getElementById(i))).join(' ').trim(); if (t) return t; }
    }
    let p = e.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      for (const l of p.querySelectorAll('label, legend, [class*="label"]')) {
        if (l.contains(e) || (skip && skip.has(l))) continue;
        const t = txt(l); if (t && t.length < 300) return t;
      }
    }
    return e.placeholder || e.name || '';
  };
  const out = []; const seen = new Set();
  for (const e of document.querySelectorAll('input, textarea, select')) {
    const tag = e.tagName.toLowerCase(), type = (e.type || tag).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type) || e.disabled || e.readOnly) continue;
    if (type === 'radio' || type === 'checkbox') { if (e.closest('[hidden],[aria-hidden="true"]')) continue; }
    else if (type !== 'file' && !vis(e)) continue;
    const required = e.required || e.getAttribute('aria-required') === 'true';
    if (type === 'radio') {
      const name = e.name; if (!name || seen.has(name)) continue; seen.add(name);
      const group = [...document.querySelectorAll('input[type=radio]')].filter(r => r.name === name);
      const skip = new Set(group.flatMap(r => r.labels ? [...r.labels] : []));
      const fs = e.closest('fieldset, [role=radiogroup], [role=group]');
      let label = fs ? (txt(fs.querySelector('legend')) || fs.getAttribute('aria-label') || '') : '';
      if (!label) label = labelOf(e, skip, true);
      out.push({jb: idOf(e), kind: 'radio', label, required: required || group.some(r => r.required),
        options: group.map(r => txt(r.labels && r.labels[0]) || r.value), optionIds: group.map(r => idOf(r)),
        value: group.some(r => r.checked) ? 'x' : ''});
      continue;
    }
    let kind = tag === 'textarea' ? 'textarea' : tag === 'select' ? 'select' : type === 'checkbox' ? 'checkbox'
      : type === 'file' ? 'file' : 'text';
    if (kind === 'text' && (e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete'))) kind = 'combobox';
    const label = labelOf(e);
    const item = {jb: idOf(e), kind, inputType: type, label, required: required || /\*\s*$/.test(label)};
    if (kind === 'select') { item.options = [...e.options].filter(o => o.value !== '' && !/^(select|choose|please)/i.test(o.text.trim())).map(o => o.text.trim()); item.value = e.value; }
    if (kind === 'text' || kind === 'textarea') item.value = e.value;
    if (kind === 'checkbox') item.value = e.checked ? 'x' : '';
    out.push(item);
  }
  return out;
}"""

IDENT = [(r"^first name|given name", "first"), (r"^last name|surname|family name", "last"),
         (r"^(full |legal |your )?name$", "name"), (r"e-?mail", "email"), (r"phone|mobile|contact number", "phone"),
         (r"linkedin", "linkedin"), (r"github", "github"), (r"website|portfolio|personal site", "website"),
         (r"^(current )?(location|city)", "location")]


class Cancelled(Exception):
    pass


class Ctx:
    def __init__(self, page, job, master, master_txt):
        self.page, self.job, self.master, self.master_txt = page, job, master, master_txt
        c = master["contact"]
        first, _, last = c["name"].partition(" ")
        self.vals = {**c, "first": first, "last": last or first}
        self.skipped, self.failed = [], []
        self._cover = None

    def cover_pdf(self):
        if not self._cover:
            out = ROOT / "data" / "resumes"
            out.mkdir(parents=True, exist_ok=True)
            p = out / f"Cover_{safe_name(self.job['company'], self.job['title'])}.pdf"
            self._cover = str(build_cover_pdf(self.job.get("cover") or "", self.master["contact"], p))
        return self._cover


def _resolve(f, ctx):
    """Value we already know for this field, or None."""
    low, kind = f["label"].lower(), f["kind"]
    if kind == "file":
        if "cover" in low:
            return ctx.cover_pdf() if ctx.job.get("cover") else None
        return ctx.job["pdf_path"] if re.search(r"resume|cv|upload|attach", low) or f["required"] else None
    if kind == "textarea" and "cover letter" in low:
        return ctx.job.get("cover") or None
    if kind in ("text", "combobox"):
        for pat, key in IDENT:
            if re.search(pat, low) and ctx.vals.get(key):
                return ctx.vals[key]
    ans = memory.lookup(f["label"], f.get("options"))
    if ans is None:
        return None
    if kind in ("select", "radio"):
        return memory.pick_option(ans, f["options"])
    return ans


def _ask(ctx, f):
    sug = ai.draft_answer(ctx.job, ctx.master_txt, f["label"]) if f["kind"] == "textarea" else ""
    opts = ["Yes", "No"] if f["kind"] == "checkbox" else (f.get("options") or [])
    qid = db.add_question(ctx.job["id"], f["label"], f["kind"], json.dumps(opts), sug)
    db.update(ctx.job["id"], status="awaiting_input")
    while True:
        q = db.get_question(qid)
        if q["answer"] is not None:
            break
        if (db.get(ctx.job["id"]) or {}).get("status") == "cancelled":
            raise Cancelled()
        ctx.page.wait_for_timeout(1000)
    db.update(ctx.job["id"], status="filling")
    if q["answer"] == SKIP:
        return None
    if q["remember"] and f["kind"] != "textarea":
        memory.remember(f["label"], q["answer"], f["kind"])
    if f["kind"] in ("select", "radio"):
        return memory.pick_option(q["answer"], f["options"]) or q["answer"]
    return q["answer"]


def _apply(target, ctx, f, v):
    loc = target.locator(f'[data-jb="{f["jb"]}"]')
    k = f["kind"]
    if k in ("text", "textarea"):
        loc.fill(v, timeout=5000)
    elif k == "combobox":
        loc.click(timeout=5000)
        loc.fill(v)
        ctx.page.wait_for_timeout(700)
        loc.press("Enter")
    elif k == "select":
        loc.select_option(label=v, timeout=5000)
    elif k == "radio":
        target.locator(f'[data-jb="{f["optionIds"][f["options"].index(v)]}"]').check(force=True, timeout=5000)
    elif k == "checkbox":
        (loc.check if str(v).strip().lower() in YES else loc.uncheck)(force=True, timeout=5000)
    elif k == "file":
        loc.set_input_files(v, timeout=8000)


def _handle(target, ctx, f):
    if f.get("value") or not f["label"]:
        return
    v = _resolve(f, ctx)
    if v is None:
        if f["kind"] == "file" or (not f["required"] and not cfg.get("ask_optional")):
            ctx.skipped.append(f["label"])
            return
        v = _ask(ctx, f)
        if v is None:
            ctx.skipped.append(f["label"])
            return
    try:
        _apply(target, ctx, f, v)
    except Cancelled:
        raise
    except Exception as e:
        ctx.failed.append(f"{f['label']} ({str(e).splitlines()[0][:70]})")


def _fill_target(target, ctx):
    done, total = set(), 0
    for _ in range(4):  # extra passes catch fields revealed by earlier answers
        fields = [f for f in target.evaluate(SCAN_JS) if f["jb"] not in done]
        if not fields:
            break
        total += len(fields)
        for f in fields:
            done.add(f["jb"])
            _handle(target, ctx, f)
        ctx.page.wait_for_timeout(800)
    return total


def _apply_url(job):
    u = job["apply_url"].rstrip("/")
    if job["source"] == "ashby" and not u.endswith("application"):
        u += "/application"
    if job["source"] == "lever" and not u.endswith("apply"):
        u += "/apply"
    return u


def fill_job(page, job, master, master_txt):
    ctx = Ctx(page, job, master, master_txt)
    page.goto(_apply_url(job), wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3000)
    seen = 0
    for attempt in range(2):
        for t in [page, *page.frames[1:]]:  # also look inside embedded (iframe) forms
            seen = _fill_target(t, ctx)
            if seen >= 3:
                break
        if seen >= 3 or attempt:
            break
        try:  # landed on a job description page: click through to the form
            page.get_by_role("link", name=re.compile(r"^apply( now| for this job| here)?$", re.I)).or_(
                page.get_by_role("button", name=re.compile(r"^apply( now| for this job| here)?$", re.I))
            ).first.click(timeout=4000)
            page.wait_for_timeout(3000)
        except Exception:
            break
    notes = []
    if seen < 3:
        notes.append("Couldn't find an application form on this page - open the application manually.")
    if ctx.skipped:
        notes.append("Left blank: " + "; ".join(ctx.skipped))
    if ctx.failed:
        notes.append("Couldn't fill: " + "; ".join(ctx.failed))
    captcha = any("captcha" in (fr.get_attribute("src") or "").lower() for fr in page.locator("iframe").all())
    if captcha:
        notes.append("Captcha on the page - you'll need to solve it.")
    if cfg["auto_click_submit"] and seen >= 3 and not ctx.failed and not captcha:
        page.get_by_role("button", name=re.compile(r"submit|apply", re.I)).last.click()
        page.wait_for_timeout(4000)
        notes.append("Submit was clicked automatically - confirm in the browser tab.")
    db.update(job["id"], status="filled", notes="\n".join(notes))


# ---- single background worker that owns the (visible) browser -------------------------------------------------
_jobs = queue.Queue()
_started = False


def enqueue(job_id):
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_worker, daemon=True).start()
    _jobs.put(job_id)


def _worker():
    master, master_txt = load_master()
    with sync_playwright() as p:
        def launch():
            return p.chromium.launch_persistent_context(str(ROOT / "data" / "browser"), headless=False, viewport=None)
        browser = launch()
        while True:
            job = db.get(_jobs.get())
            if not job or job["status"] not in ("approved", "filling", "awaiting_input"):
                continue
            db.update(job["id"], status="filling")
            try:
                try:
                    page = browser.new_page()
                except Exception:  # user closed the window - reopen
                    browser = launch()
                    page = browser.new_page()
                fill_job(page, job, master, master_txt)
            except Cancelled:
                pass
            except Exception as e:
                db.update(job["id"], status="error", notes=str(e).splitlines()[0][:300])
