"""The interface: paste a link -> tailored resume queued -> approve -> form is filled in a visible browser.
It asks you (here) only for answers it doesn't remember. Nothing is submitted without you."""
import json
import secrets
import socket
import threading
import time
import webbrowser
from datetime import timedelta

from flask import Flask, redirect, render_template_string, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, fetchjob, formfill, memory, usage
from .config import ROOT, cfg, load_master
from .pipeline import process_link, run_fetch

AUTH = ROOT / "profile" / "auth.json"     # {email, hash} - never the plain password; git-ignored
SECRET = ROOT / "profile" / "secret.key"  # signs the login cookie; git-ignored


def _secret():
    if not SECRET.exists():
        SECRET.parent.mkdir(exist_ok=True)
        SECRET.write_text(secrets.token_hex(32))
    return SECRET.read_text().strip()


def _auth():
    try:
        return json.loads(AUTH.read_text())
    except FileNotFoundError:
        return None


def set_login(email, password):
    if len(password) < 8:
        raise SystemExit("Use a password of at least 8 characters.")
    AUTH.parent.mkdir(exist_ok=True)
    AUTH.write_text(json.dumps({"email": email.strip(), "hash": generate_password_hash(password)}))
    SECRET.unlink(missing_ok=True)  # new key: every existing login cookie is logged out


app = Flask(__name__)
app.secret_key = _secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30))
TASKS = []  # in-memory progress of pasted links
_fails = {}  # ip -> recent failed login times

ASSETS_V = "4"  # bump to bust phone/browser caches after editing static/glass.*
CSS = f"""<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/glass.css?v={ASSETS_V}"><script src="/static/glass.js?v={ASSETS_V}" defer></script>"""

NAV = """<nav><b>Job applier</b><a href="/">Apply</a><a href="/memory">Remembered answers</a>
<form method="post" action="/logout" style="margin-left:auto"><button>Log out</button></form></nav>"""

LOGIN = """<!doctype html><title>Log in</title>""" + CSS + """
<h2>Job applier</h2>
{% if not configured %}<div class="warn">No login set yet. On the laptop run: <code>python main.py set-login --email you@example.com</code></div>{% endif %}
<form class="box" method="post">
 {% if err %}<div class="warn">{{ err }}</div>{% endif %}
 <label>Email<input type="text" name="email" autocomplete="username" required autofocus></label>
 <label>Password<input type="password" name="password" autocomplete="current-password" required></label>
 <label><input type="checkbox" name="remember" checked> Keep me logged in on this device (30 days)</label><br>
 <button class="ok">Log in</button></form>"""

PANEL = """
{% for t in tasks %}<div class="box"><span class="chip st-{{ t.state }}">{{ {'working':'working','done':'done','error':'problem'}[t.state] }}</span>
 <b>{{ t.url[:70] }}</b> <span class="meta">{{ t.msg }}</span></div>{% endfor %}
{% for q in questions %}<form class="box ask" method="post" action="/answer/{{ q.id }}">
 <b>{{ q.label }}</b> <span class="meta">({{ q.title }} - {{ q.company }})</span>
 {% if q.opts %}<select name="answer">{% for o in q.opts %}<option>{{ o }}</option>{% endfor %}</select>
 {% elif q.kind == 'textarea' %}<textarea name="answer" rows="6">{{ q.suggestion }}</textarea>
 <div class="meta">Drafted from your resume - edit before saving.</div>
 {% else %}<input type="text" name="answer" autofocus>{% endif %}
 {% if q.kind != 'textarea' %}<label><input type="checkbox" name="remember" checked> Remember for next time</label><br>{% endif %}
 <button class="ok">Save &amp; continue</button> <button name="skip" value="1" formnovalidate>Leave blank</button></form>{% endfor %}
{% for j in active %}<div class="box"><span class="chip st-{{ j.status }}">{{ {'approved':'waiting for the browser','filling':'filling the form','awaiting_input':'needs your answer','filled':'form filled - review the browser window'}[j.status] }}</span>
 <b>{{ j.title }} - {{ j.company }}</b>
 {% if j.notes %}<div class="note">{{ j.notes }}</div>{% endif %}
 {% if j.status == 'filled' %}<form method="post" action="/act/{{ j.id }}/submitted" style="display:inline"><button class="ok">I submitted it</button></form>
 <form method="post" action="/act/{{ j.id }}/needs_manual" style="display:inline"><button>Didn't go through</button></form>
 {% else %}<form method="post" action="/act/{{ j.id }}/cancelled" style="display:inline"><button class="no">Cancel</button></form>{% endif %}</div>{% endfor %}"""

QUEUE = """
<h3>Approval queue ({{ jobs|length }}) <span class="meta">{{ counts }}</span></h3>
{% for j in jobs %}<details><summary>{{ j.title }} - {{ j.company }} <span class="meta">| {{ j.location }} | fit {{ j.score if j.score is not none else '-' }} | {{ ('%.1f LPA' % j.salary_lpa) if j.salary_lpa else 'salary not listed' }} | {{ j.source }}</span></summary>
 {% if j.score_reason %}<p>{{ j.score_reason }}</p>{% endif %}
 {% for w in j.warns %}<div class="warn">{{ w }}</div>{% endfor %}
 <p><a href="{{ j.url }}" target="_blank">Open job posting</a> | <a href="/pdf/{{ j.id }}" target="_blank">Open tailored PDF</a></p>
 <h4>Keywords mapped to real evidence</h4>
 <ul>{% for k in j.r.keywords_mapped %}<li><b>{{ k.keyword }}</b>: {{ k.evidence }}</li>{% endfor %}</ul>
 <h4>Resume text</h4><pre>{{ j.r.summary }}
{% for e in j.r.experience %}
{{ e.title }}, {{ e.company }}
{% for b in e.bullets %}- {{ b }}
{% endfor %}{% endfor %}{% for p in j.r.projects %}
PROJECT {{ p.name }}
{% for b in p.bullets %}- {{ b }}
{% endfor %}{% endfor %}</pre>
 <h4>Cover letter</h4><pre>{{ j.cover }}</pre>
 {% if j.source == 'manual' %}
 <p class="meta">This site isn't automated. Apply yourself with the PDF above, then:</p>
 <form method="post" action="/act/{{ j.id }}/applied_manual" style="display:inline"><button class="ok">I applied</button></form>
 {% else %}<form method="post" action="/act/{{ j.id }}/approved" style="display:inline"><button class="ok">Approve - fill the form</button></form>{% endif %}
 <form method="post" action="/act/{{ j.id }}/rejected" style="display:inline"><button class="no">Reject</button></form>
</details>{% else %}<p class="meta">Nothing waiting for approval.</p>{% endfor %}"""

INDEX = """<!doctype html><title>Job applier</title>""" + CSS + NAV + """
<div class="dash">
 <div class="gauge" data-value="{{ per_app }}" data-max="50000" data-label="TOKENS / APPLICATION"></div>
 <div id="usage" class="box meta">{{ usage }}</div>
</div>
<h2>Paste a job link</h2>
<form class="box" method="post" action="/add">
 <input type="url" name="url" placeholder="https://jobs.lever.co/... / boards.greenhouse.io/... / company careers page" required>
 <textarea name="pasted" rows="3" placeholder="Only needed for LinkedIn / Indeed / Wellfound / Talboss or pages I can't read: paste the job description here"></textarea>
 <button class="ok">Tailor my resume</button></form>
<form method="post" action="/login-browser" style="margin-bottom:8px"><button>Open jobbot browser to log in</button>
 <span class="meta">log in to LinkedIn / Naukri / Indeed / Wellfound once in that window - it stays logged in</span></form>
<form method="post" action="/fetch"><button>Find matching jobs now</button>
 <span class="meta">software jobs, 6+ LPA or salary not listed; searches automatically every few hours while this page's app is running</span></form>
<div id="panel">{{ panel|safe }}</div>
<div id="queue">{{ queue|safe }}</div>
<script>
async function poll(id, url, skip){ const el=document.getElementById(id);
 if(skip(el)) return; try{ const r=await fetch(url); if(r.status===401){location.href='/login';return;}
 const t=await r.text(); if(t!==el._last){ el._last=t; el.innerHTML=t; } }catch(e){} }
setInterval(()=>poll('panel','/panel',el=>el.contains(document.activeElement)&&document.activeElement.tagName!=='BODY'),3000);
setInterval(()=>poll('usage','/usage',()=>false),5000);
setInterval(()=>poll('queue','/queue',el=>el.querySelector('details[open]')),5000);
</script>"""

MEMORY = """<!doctype html><title>Remembered answers</title>""" + CSS + NAV + """
<h2>Remembered answers</h2><p class="meta">Used automatically on any form. Fix anything wrong here.</p>
{% for k, v in items %}<form class="box" method="post" action="/memory/save"><input type="hidden" name="key" value="{{ k }}">
 <b>{{ v.label }}</b><input type="text" name="answer" value="{{ v.answer }}">
 <button class="ok">Save</button> <button class="no" formaction="/memory/delete">Delete</button></form>{% endfor %}
<form class="box" method="post" action="/memory/add"><b>Add one</b>
 <input type="text" name="label" placeholder="Question, e.g. Notice period" required>
 <input type="text" name="answer" placeholder="Answer" required><button class="ok">Add</button></form>"""

ACTIVE = ("approved", "filling", "awaiting_input", "filled")


def _panel():
    qs = db.pending_questions()
    for q in qs:
        q["opts"] = json.loads(q["options"] or "[]")
    active = [j for s in ACTIVE for j in db.by_status(s)]
    return render_template_string(PANEL, tasks=TASKS[-5:], questions=qs, active=active)


def _queue():
    jobs = db.by_status("queued", "score DESC")
    for j in jobs:
        j["r"] = json.loads(j["resume_json"])
        j["warns"] = json.loads(j["warnings"] or "[]")
    return render_template_string(QUEUE, jobs=jobs, counts=db.counts())


@app.before_request
def require_login():
    if request.endpoint in ("login", "static") or session.get("user"):
        return None
    if request.path in ("/panel", "/queue", "/usage"):
        return "", 401
    return redirect("/login?next=" + request.path)


@app.route("/login", methods=["GET", "POST"])
def login():
    err, a = "", _auth()
    if request.method == "POST":
        ip, now = request.remote_addr, time.time()
        recent = [t for t in _fails.get(ip, []) if now - t < 900]
        if len(recent) >= 5:
            err = "Too many attempts. Try again in 15 minutes."
        elif a and request.form.get("email", "").strip().lower() == a["email"].lower() \
                and check_password_hash(a["hash"], request.form.get("password", "")):
            session.clear()
            session.permanent = bool(request.form.get("remember"))
            session["user"] = a["email"]
            _fails.pop(ip, None)
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        else:
            recent.append(now)
            err = "Wrong email or password."
        _fails[ip] = recent
    return render_template_string(LOGIN, err=err, configured=bool(a))


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/")
def index():
    s = usage.summary()
    per_app = s["per_app"] or usage.ESTIMATE_PER_APP
    return render_template_string(INDEX, panel=_panel(), queue=_queue(), usage=usage.text(), per_app=round(per_app))


@app.get("/usage")
def usage_fragment():
    return usage.text()


@app.get("/panel")
def panel():
    return _panel()


@app.get("/queue")
def queue():
    return _queue()


def _process(task, url, pasted):
    try:
        _, msg = process_link(url, pasted, say=lambda m: task.update(msg=m))
        task.update(state="done", msg=msg)
    except (Exception, SystemExit) as e:
        task.update(state="error", msg=str(e))


@app.post("/add")
def add():
    task = {"url": request.form["url"].strip(), "state": "working", "msg": "reading the job and tailoring your resume..."}
    TASKS.append(task)
    threading.Thread(target=_process, args=(task, task["url"], request.form.get("pasted", "")), daemon=True).start()
    return redirect("/")


def _search(task):
    try:
        run_fetch(lambda m: task.update(msg=m))
        task.update(state="done")
    except (Exception, SystemExit) as e:
        task.update(state="error", msg=str(e))


@app.post("/login-browser")
def login_browser():
    from . import platforms

    def open_tabs(b):
        for n in platforms.PLATFORMS:
            if n in platforms.enabled():
                b.new_page().goto(platforms.LOGIN[n], wait_until="domcontentloaded")

    threading.Thread(target=lambda: formfill.run_in_browser(open_tabs), daemon=True).start()
    return redirect("/")


@app.post("/fetch")
def fetch():
    task = {"url": "Job search", "state": "working", "msg": "starting..."}
    TASKS.append(task)
    threading.Thread(target=_search, args=(task,), daemon=True).start()
    return redirect("/")


def _auto_search_loop(hours):
    time.sleep(90)  # let the app settle first
    while True:
        task = {"url": "Scheduled job search", "state": "working", "msg": "starting..."}
        TASKS.append(task)
        _search(task)
        time.sleep(hours * 3600)


@app.post("/answer/<int:qid>")
def answer(qid):
    if request.form.get("skip"):
        db.answer_question(qid, formfill.SKIP, False)
    else:
        db.answer_question(qid, request.form.get("answer", "").strip(), bool(request.form.get("remember")))
    return redirect("/")


@app.post("/act/<job_id>/<status>")
def act(job_id, status):
    if status not in ("approved", "applied_manual", "rejected", "submitted", "needs_manual", "cancelled"):
        return "bad", 400
    db.update(job_id, status=status)
    if status == "approved":
        formfill.enqueue(job_id)
    return redirect("/")


@app.get("/pdf/<job_id>")
def pdf(job_id):
    return send_file(db.get(job_id)["pdf_path"])


@app.get("/memory")
def memory_page():
    return render_template_string(MEMORY, items=sorted(memory.load().items(), key=lambda kv: kv[1]["label"].lower()))


@app.post("/memory/save")
def memory_save():
    d = memory.load()
    if request.form["key"] in d:
        d[request.form["key"]]["answer"] = request.form["answer"]
        memory.save(d)
    return redirect("/memory")


@app.post("/memory/delete")
def memory_delete():
    memory.forget(request.form["key"])
    return redirect("/memory")


@app.post("/memory/add")
def memory_add():
    memory.remember(request.form["label"], request.form["answer"])
    return redirect("/memory")


def run():
    for s in ("filling", "awaiting_input"):  # left over from a previous run: re-queue
        for j in db.by_status(s):
            db.update(j["id"], status="approved")
    for j in db.by_status("approved", "created ASC"):  # oldest first: applications go out in queue order
        formfill.enqueue(j["id"])
    from . import cloudsync
    cloudsync.start()
    if cfg.get("auto_fetch_hours"):
        threading.Thread(target=_auto_search_loop, args=(cfg["auto_fetch_hours"],), daemon=True).start()
    srv = cfg.get("server") or {}
    host, port = srv.get("host", "0.0.0.0"), srv.get("port", 5000)
    if not _auth():
        print("No login set yet: run  python main.py set-login --email you@example.com")
    if host == "0.0.0.0":
        print(f"On your phone (same Wi-Fi): http://{_lan_ip()}:{port}")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, threaded=True, use_reloader=False)


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
