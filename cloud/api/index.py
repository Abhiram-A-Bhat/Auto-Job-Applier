"""Resume tailor website (runs on Vercel). Paste a job link here; the laptop tailors it and uploads the PDF back.

Env vars: APP_EMAIL, APP_PASSWORD_HASH, SECRET_KEY, AGENT_TOKEN, and REDIS_URL
          (or KV_REST_API_URL + KV_REST_API_TOKEN / UPSTASH_REDIS_REST_URL + _TOKEN).
"""
import base64
import hmac
import json
import os
import re
import time
import uuid
from datetime import timedelta

import httpx
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session
from werkzeug.security import check_password_hash


# ---- storage: Redis (in-memory fallback for local testing) ----------------------------------------------------
class RedisStore:
    def __init__(self, url, token):
        self.url, self.h = url.rstrip("/"), {"Authorization": f"Bearer {token}"}

    def cmd(self, *args):
        r = httpx.post(self.url, headers=self.h, json=[str(a) for a in args], timeout=10)
        r.raise_for_status()
        return r.json().get("result")


class MemStore:
    def __init__(self):
        self.kv, self.z = {}, {}

    def cmd(self, op, *a):
        op = op.upper()
        if op == "GET":
            return self.kv.get(a[0])
        if op == "SET":
            self.kv[a[0]] = str(a[1])
            return "OK"
        if op == "DEL":
            return 1 if self.kv.pop(a[0], None) is not None else 0
        if op == "INCR":
            self.kv[a[0]] = str(int(self.kv.get(a[0], 0)) + 1)
            return int(self.kv[a[0]])
        if op == "EXPIRE":
            return 1
        if op == "ZADD":
            self.z.setdefault(a[0], {})[str(a[2])] = float(a[1])
            return 1
        if op == "ZREM":
            self.z.get(a[0], {}).pop(str(a[1]), None)
            return 1
        if op == "ZRANGE":
            return [m for m, _ in sorted(self.z.get(a[0], {}).items(), key=lambda x: x[1])]
        if op == "MGET":
            return [self.kv.get(k) for k in a]
        raise ValueError(op)


class RedisTcpStore:
    """Redis Cloud / any redis:// URL (Vercel's official Redis integration sets REDIS_URL)."""
    def __init__(self, url):
        import redis
        self.r = redis.from_url(url, decode_responses=True, socket_timeout=10)

    def cmd(self, *args):
        return self.r.execute_command(*[str(a) for a in args])


_url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
_tok = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
_redis_url = os.environ.get("REDIS_URL") or os.environ.get("KV_URL")
store = (RedisStore(_url, _tok) if _url and _tok else
         RedisTcpStore(_redis_url) if _redis_url else MemStore())
KEEP = 100  # newest resumes kept online
MAX_PDF = int(2.5 * 1024 * 1024)


def _items():
    ids = store.cmd("ZRANGE", "resumes", 0, -1) or []
    vals = store.cmd("MGET", *["res:" + i for i in ids]) if ids else []
    return [json.loads(v) for v in vals if v]


def _load(i):
    v = store.cmd("GET", "res:" + i)
    return json.loads(v) if v else None


def _save(it):
    store.cmd("SET", "res:" + it["id"], json.dumps(it))
    store.cmd("ZADD", "resumes", it["created"], it["id"])


def _remove(i):
    for key in ("res:" + i, f"file:{i}:pdf", f"file:{i}:tex"):
        store.cmd("DEL", key)
    store.cmd("ZREM", "resumes", i)


def _bump():  # tells the laptop something changed, so idle polls stay cheap
    store.cmd("INCR", "resumes:ver")


# ---- app + login ---------------------------------------------------------------------------------------------
# on Vercel, /static is served straight from cloud/public/static; Flask serves it when running locally
app = Flask(__name__, static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public", "static"))
ON_VERCEL = bool(os.environ.get("VERCEL"))
app.secret_key = os.environ.get("SECRET_KEY") or ("" if ON_VERCEL else "local-dev-only")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=ON_VERCEL,
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30))
EMAIL = os.environ.get("APP_EMAIL", "")
PW_HASH = os.environ.get("APP_PASSWORD_HASH", "")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
LABEL = {"waiting": "waiting for the laptop", "processing": "tailoring on the laptop...", "done": "ready",
         "error": "couldn't make it"}

ASSETS_V = "4"  # cloud/public/static is a copy of jobbot/static
CSS = f"""<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/glass.css?v={ASSETS_V}"><script src="/static/glass.js?v={ASSETS_V}" defer></script>"""

LOGIN = "<!doctype html><title>Log in</title>" + CSS + """
<h2>Resume tailor</h2>{% if msg %}<div class="warn">{{ msg }}</div>{% endif %}
<form class="box" method="post">
 <label>Email<input type="text" name="email" autocomplete="username" required autofocus></label>
 <label>Password<input type="password" name="password" autocomplete="current-password" required></label>
 <label><input type="checkbox" name="remember" checked> Keep me logged in on this device (30 days)</label><br>
 <button class="ok">Log in</button></form>"""

HOME = "<!doctype html><title>Resume tailor</title>" + CSS + """
<nav><b>Resume tailor</b><span class="led {{ 'on' if online else 'off' }}">{{ laptop }}</span>
<form method="post" action="/logout" style="margin-left:auto"><button>Log out</button></form></nav>
<main id="main">
<h2>Tailor my resume</h2>
<form class="box" method="post" action="/add">
 <input type="url" name="url" placeholder="Paste the job link" required>
 <textarea name="pasted" rows="3" placeholder="Only if the link needs a login: paste the job description here"></textarea>
 <button class="ok">Make PDF</button>
 <div class="meta">Your laptop tailors links in the order you add them. If it's off, they wait here.</div></form>
<div id="list">
{% for i in items %}<div class="box"><span class="chip st-{{ i.status }}">{{ label[i.status] }}</span>
 <b>{% if i.role %}{{ i.role }} - {{ i.company }}{% else %}{{ i.url[:70] }}{% endif %}</b>
 {% if i.status == 'done' %}{% if i.note %}<div class="warn">{{ i.note }}</div>{% endif %}
 <p><a href="/file/{{ i.id }}/pdf" target="_blank" rel="noopener">Open PDF</a> | <a href="/file/{{ i.id }}/pdf?dl=1">Download PDF</a> |
 <a href="/file/{{ i.id }}/tex">Download LaTeX</a> | <a href="{{ i.url }}" target="_blank" rel="noopener">Job posting</a></p>
 {% elif i.status == 'error' %}<div class="warn">{{ i.error }}</div>
 <form method="post" action="/retry/{{ i.id }}"><textarea name="pasted" rows="3" placeholder="Paste the job description here if the link needs a login"></textarea>
 <button class="ok">Try again</button></form>{% endif %}
 {% if i.status != 'processing' %}<form method="post" action="/remove/{{ i.id }}" style="display:inline"><button class="no">Remove</button></form>{% endif %}
</div>{% else %}<p class="meta">Nothing yet. Paste a link above.</p>{% endfor %}
</div></main>
<script>
setInterval(async () => {
  const l = document.getElementById('list'), a = document.activeElement;
  if (a && a.closest && a.closest('#list form')) return;
  try {
    const r = await fetch('/');
    if (r.redirected) { location.href = '/login'; return; }
    const d = new DOMParser().parseFromString(await r.text(), 'text/html'), n = d.getElementById('list'), led = d.querySelector('nav .led');
    if (n && n.innerHTML !== l._last) { l._last = n.innerHTML; l.innerHTML = n.innerHTML; }
    const cur = document.querySelector('nav .led');
    if (led && cur) { cur.className = led.className; cur.textContent = led.textContent; }
  } catch (e) {}
}, 6000);
</script>"""


def _ip():
    return request.headers.get("x-real-ip") or request.remote_addr or "?"


@app.before_request
def guard():
    if request.path.startswith("/api/agent/"):
        ok = AGENT_TOKEN and hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + AGENT_TOKEN)
        return None if ok else (jsonify(error="unauthorized"), 401)
    if request.endpoint == "static":
        return None
    if ON_VERCEL and isinstance(store, MemStore):
        return "Storage not connected: add a Redis integration to this Vercel project, then redeploy.", 500
    if not app.secret_key or not PW_HASH:
        return "Server not configured: set SECRET_KEY, APP_EMAIL, APP_PASSWORD_HASH, AGENT_TOKEN.", 500
    if request.endpoint != "login" and not session.get("user"):
        return redirect("/login")
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    msg = ""
    if request.method == "POST":
        key = "fail:" + _ip()
        if int(store.cmd("GET", key) or 0) >= 5:
            msg = "Too many attempts. Try again in 15 minutes."
        elif hmac.compare_digest(request.form.get("email", "").strip().lower(), EMAIL.lower()) \
                and check_password_hash(PW_HASH, request.form.get("password", "")):
            store.cmd("DEL", key)
            session.clear()
            session.permanent = bool(request.form.get("remember"))
            session["user"] = EMAIL
            return redirect("/")
        else:
            if store.cmd("INCR", key) == 1:
                store.cmd("EXPIRE", key, 900)
            msg = "Wrong email or password."
    return render_template_string(LOGIN, msg=msg)


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/")
def home():
    ago = time.time() - float(store.cmd("GET", "agent:seen") or 0)
    online = ago < 120
    laptop = "Laptop online" if online else "Laptop offline - links wait here"
    items = sorted(_items(), key=lambda i: -i["created"])
    return render_template_string(HOME, items=items, online=online, laptop=laptop, label=LABEL)


@app.post("/add")
def add():
    url = request.form.get("url", "").strip()
    if url.startswith(("http://", "https://")):
        _save({"id": uuid.uuid4().hex[:12], "url": url, "pasted": request.form.get("pasted", "")[:20000],
               "created": time.time(), "status": "waiting", "attempt": 0})
        _bump()
    return redirect("/")


@app.post("/retry/<item_id>")
def retry(item_id):
    it = _load(item_id)
    if it and it["status"] == "error":
        it.update(status="waiting", error="", attempt=it.get("attempt", 0) + 1)
        it["pasted"] = request.form.get("pasted", "").strip()[:20000] or it.get("pasted", "")
        _save(it)
        _bump()
    return redirect("/")


@app.post("/remove/<item_id>")
def remove(item_id):
    it = _load(item_id)
    if it and it["status"] != "processing":
        _remove(item_id)
        _bump()
    return redirect("/")


@app.get("/file/<item_id>/<kind>")
def download(item_id, kind):
    it = _load(item_id)
    data = store.cmd("GET", f"file:{item_id}:{kind}") if it and kind in ("pdf", "tex") else None
    if not data:
        return "Not found", 404
    body = base64.b64decode(data) if kind == "pdf" else data.encode("utf-8")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", it.get("name") or "resume") + "." + kind
    how = "inline" if kind == "pdf" and not request.args.get("dl") else "attachment"
    return Response(body, mimetype="application/pdf" if kind == "pdf" else "application/x-tex",
                    headers={"Content-Disposition": f'{how}; filename="{name}"', "X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, no-store"})


# ---- laptop agent API (Bearer AGENT_TOKEN) -------------------------------------------------------------------
@app.post("/api/agent/pull")
def agent_pull():
    store.cmd("SET", "agent:seen", time.time())
    ver = str(store.cmd("GET", "resumes:ver") or "0")
    if (request.get_json(silent=True) or {}).get("ver") == ver:
        return jsonify(ver=ver, unchanged=True)
    todo = [i for i in _items() if i["status"] in ("waiting", "processing")]  # processing: laptop stopped mid-way
    return jsonify(ver=ver, todo=sorted(todo, key=lambda i: i["created"]))


@app.post("/api/agent/status")
def agent_status():
    d = request.get_json(force=True)
    it = _load(d.get("id", ""))
    if not it:
        return jsonify(ok=False), 404
    if d.get("status") in ("processing", "error"):
        it.update(status=d["status"], error=str(d.get("error", ""))[:500])
        _save(it)
    return jsonify(ok=True)


@app.post("/api/agent/upload")
def agent_upload():
    d = request.get_json(force=True)
    it = _load(d.get("id", ""))
    if not it:
        return jsonify(ok=False), 404  # you removed it while it was being made
    try:
        pdf = base64.b64decode(d["pdf"], validate=True)
    except (KeyError, ValueError):
        return jsonify(error="bad pdf"), 400
    if not pdf.startswith(b"%PDF") or len(pdf) > MAX_PDF:
        return jsonify(error="bad pdf"), 400
    store.cmd("SET", f"file:{it['id']}:pdf", d["pdf"])
    store.cmd("SET", f"file:{it['id']}:tex", str(d.get("tex", "")))
    it.update(status="done", error="", role=str(d.get("role", ""))[:120], company=str(d.get("company", ""))[:120],
              name=str(d.get("name", "resume"))[:120], note=str(d.get("note", ""))[:500], pasted="")
    _save(it)
    for old in (store.cmd("ZRANGE", "resumes", 0, -1) or [])[:-KEEP]:
        _remove(old)
    return jsonify(ok=True)
