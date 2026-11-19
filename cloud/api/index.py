"""Cloud inbox (runs on Vercel). Paste links, approve jobs and answer form questions from anywhere.
The laptop agent (jobbot/cloudsync.py) pulls everything in order whenever the laptop app is running.

Env vars: APP_EMAIL, APP_PASSWORD_HASH, SECRET_KEY, AGENT_TOKEN,
          KV_REST_API_URL + KV_REST_API_TOKEN (or UPSTASH_REDIS_REST_URL/_TOKEN) from the Upstash Redis integration.
"""
import hmac
import json
import os
import time
import uuid
from datetime import timedelta

import httpx
from flask import Flask, jsonify, redirect, render_template_string, request, session
from werkzeug.security import check_password_hash


# ---- storage: Upstash Redis over REST (in-memory fallback for local testing) ---------------------------------
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


def _all(zkey, prefix):
    ids = store.cmd("ZRANGE", zkey, 0, -1) or []
    vals = store.cmd("MGET", *[prefix + i for i in ids]) if ids else []
    return [json.loads(v) for v in vals if v]


def _load(prefix, i):
    v = store.cmd("GET", prefix + i)
    return json.loads(v) if v else None


def _put(prefix, zkey, obj, score):
    store.cmd("SET", prefix + obj["id"], json.dumps(obj))
    store.cmd("ZADD", zkey, score, obj["id"])


def _delete(prefix, zkey, i):
    store.cmd("DEL", prefix + i)
    store.cmd("ZREM", zkey, i)


def _bump():  # tells the laptop something changed, so idle polls cost only 2 Redis commands
    store.cmd("INCR", "ver")


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
SKIP = "__skip__"
ACTIONS = ("approved", "rejected", "applied_manual", "submitted", "needs_manual", "cancelled")
LABEL = {"waiting": "waiting for the laptop", "processing": "tailoring your resume...", "queued": "ready for approval",
         "approved": "approved - will apply in order", "filling": "filling the form...",
         "awaiting_input": "needs your answer (top of page)", "filled": "form filled", "submitted": "submitted",
         "needs_manual": "needs manual apply", "rejected": "rejected", "applied_manual": "applied manually",
         "error": "error", "cancelled": "cancelled"}

ASSETS_V = "3"  # keep in sync with jobbot/webapp.py; cloud/public/static is a copy of jobbot/static
CSS = f"""<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/glass.css?v={ASSETS_V}"><script src="/static/glass.js?v={ASSETS_V}" defer></script>"""

LOGIN = "<!doctype html><title>Log in</title>" + CSS + """
<h2>Job applier</h2>{% if msg %}<div class="warn">{{ msg }}</div>{% endif %}
<form class="box" method="post">
 <label>Email<input type="text" name="email" autocomplete="username" required autofocus></label>
 <label>Password<input type="password" name="password" autocomplete="current-password" required></label>
 <label><input type="checkbox" name="remember" checked> Keep me logged in on this device (30 days)</label><br>
 <button class="ok">Log in</button></form>"""

HOME = "<!doctype html><title>Job applier</title>" + CSS + """
<nav><b>Job applier</b><span class="led {{ 'on' if online else 'off' }}">{{ laptop }}</span>
<form method="post" action="/logout" style="margin-left:auto"><button>Log out</button></form></nav>
<main id="main">
<h2>Paste a job link</h2>
<form class="box" method="post" action="/add">
 <input type="url" name="url" placeholder="Paste a job link" required>
 <textarea name="pasted" rows="3" placeholder="Optional: paste the job description (needed only if the laptop can't open the page)"></textarea>
 <button class="ok">Add to queue</button>
 <div class="meta">Links are processed in the order you add them, as soon as the laptop app is running.</div></form>
{% for q in questions %}<form class="box ask" method="post" action="/answer/{{ q.id }}">
 <b>{{ q.label }}</b> <span class="meta">({{ q.title }} - {{ q.company }})</span>
 {% if q.options %}<select name="answer">{% for o in q.options %}<option>{{ o }}</option>{% endfor %}</select>
 {% elif q.kind == 'textarea' %}<textarea name="answer" rows="6">{{ q.suggestion }}</textarea>
 {% else %}<input type="text" name="answer">{% endif %}
 {% if q.kind != 'textarea' %}<label><input type="checkbox" name="remember" checked> Remember for next time</label><br>{% endif %}
 <button class="ok">Save</button> <button name="skip" value="1" formnovalidate>Leave blank</button></form>{% endfor %}
{% for i in items %}<details><summary>{{ i.title or i.url[:60] }}{% if i.company %} - {{ i.company }}{% endif %}
 <span class="chip st-{{ i.status }}">{{ label.get(i.status, i.status) }}</span>
 <span class="meta">{% if i.score %}fit {{ i.score }}{% endif %}{% if i.salary_lpa %} | {{ '%.1f' % i.salary_lpa }} LPA{% endif %}{% if i.action %} | {{ i.action }} pending - laptop will do it{% endif %}</span></summary>
 <p><a href="{{ i.url }}" target="_blank" rel="noopener">Open job posting</a></p>
 {% for w in i.warnings or [] %}<div class="warn">{{ w }}</div>{% endfor %}
 {% if i.notes %}<pre>{{ i.notes }}</pre>{% endif %}
 {% if i.summary %}<h4>Tailored resume</h4><pre>{{ i.summary }}</pre>{% endif %}
 {% if i.cover %}<h4>Cover letter</h4><pre>{{ i.cover }}</pre>{% endif %}
 {% for a, text, cls in buttons(i) %}<form class="inline" method="post" action="/act/{{ i.id }}/{{ a }}" style="display:inline">
 <button class="{{ cls }}">{{ text }}</button></form>{% endfor %}
</details>{% else %}<p class="meta">Nothing yet. Paste a link above.</p>{% endfor %}
</main>
<script>
setInterval(async () => {
  const m = document.getElementById('main'), a = document.activeElement;
  if (m.querySelector('details[open]') || (a && a.closest && a.closest('form'))) return;
  try {
    const r = await fetch('/', {headers: {'X-Partial': '1'}});
    if (r.redirected) { location.href = '/login'; return; }
    const d = new DOMParser().parseFromString(await r.text(), 'text/html'), n = d.getElementById('main'), led = d.querySelector('nav .led');
    if (n && n.innerHTML !== m._last) { m._last = n.innerHTML; m.innerHTML = n.innerHTML; }
    const cur = document.querySelector('nav .led');
    if (led && cur) { cur.className = led.className; cur.textContent = led.textContent; }
  } catch (e) {}
}, 15000);
</script>"""


def _buttons(i):
    s, manual = i.get("status"), i.get("source") == "manual"
    if s == "waiting":
        return [("remove", "Remove", "no")]
    if s == "queued":
        return [("applied_manual", "I applied myself", "ok") if manual else ("approved", "Approve - apply", "ok"),
                ("rejected", "Reject", "no")]
    if s == "filled":
        return [("submitted", "It went through", "ok"), ("needs_manual", "Didn't go through", "")]
    if s in ("approved", "filling", "awaiting_input"):
        return [("cancelled", "Cancel", "no")]
    return []


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
        return "Storage not connected: add the Upstash Redis integration to this Vercel project, then redeploy.", 500
    if not app.secret_key or not PW_HASH:
        return "Server not configured: set SECRET_KEY, APP_EMAIL, APP_PASSWORD_HASH, AGENT_TOKEN.", 500
    if request.endpoint not in ("login", "static") and not session.get("user"):
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
    seen = float(store.cmd("GET", "agent:seen") or 0)
    ago = time.time() - seen
    online = ago < 120
    laptop = "Laptop online" if online else (
        f"Laptop offline (last seen {int(ago // 60)} min ago) - links wait in the queue" if seen else
        "Laptop hasn't connected yet - links wait in the queue")
    items = sorted(_all("items", "item:"), key=lambda i: -i["created"])
    return render_template_string(HOME, items=items, questions=_all("questions", "q:"), online=online,
                                  laptop=laptop, label=LABEL, buttons=_buttons)


@app.post("/add")
def add():
    url = request.form.get("url", "").strip()
    if url.startswith(("http://", "https://")):
        now = time.time()
        _put("item:", "items", {"id": uuid.uuid4().hex[:12], "url": url, "pasted": request.form.get("pasted", ""),
                                "created": now, "status": "waiting", "source": "phone"}, now)
        _bump()
    return redirect("/")


@app.post("/act/<item_id>/<action>")
def act(item_id, action):
    it = _load("item:", item_id)
    if it and action == "remove" and it["status"] == "waiting":
        _delete("item:", "items", item_id)
    elif it and action in ACTIONS:
        it["action"] = action
        _put("item:", "items", it, it["created"])
    _bump()
    return redirect("/")


@app.post("/answer/<qid>")
def answer(qid):
    q = _load("q:", qid)
    if q:
        q["answer"] = SKIP if request.form.get("skip") else request.form.get("answer", "").strip()
        q["remember"] = bool(request.form.get("remember"))
        _put("q:", "questions", q, q.get("created", time.time()))
        _bump()
    return redirect("/")


# ---- laptop agent API (Bearer AGENT_TOKEN) -------------------------------------------------------------------
@app.post("/api/agent/pull")
def agent_pull():
    store.cmd("SET", "agent:seen", time.time())
    ver = store.cmd("GET", "ver") or "0"
    if (request.get_json(silent=True) or {}).get("ver") == ver:
        return jsonify(ver=ver, unchanged=True)
    items = _all("items", "item:")
    return jsonify(ver=ver,
                   waiting=[i for i in items if i["status"] in ("waiting", "processing")],  # processing: laptop died mid-way
                   actions=[{"id": i["id"], "action": i["action"], "created": i["created"]} for i in items if i.get("action")],
                   answers=[{"id": q["id"], "answer": q["answer"], "remember": q.get("remember", True)}
                            for q in _all("questions", "q:") if q.get("answer") is not None])


@app.post("/api/agent/push")
def agent_push():
    d = request.get_json(force=True)
    for u in d.get("items", []):
        cur = _load("item:", u["id"]) or {"id": u["id"], "created": u.get("created", time.time())}
        cur.update({k: v for k, v in u.items() if k not in ("action", "id")})
        _put("item:", "items", cur, cur["created"])
    for a in d.get("ack_actions", []):
        cur = _load("item:", a["id"])
        if cur and cur.get("action") == a["action"]:
            cur.pop("action")
            _put("item:", "items", cur, cur["created"])
    for q in d.get("questions", []):
        if not _load("q:", q["id"]):
            _put("q:", "questions", {**q, "answer": None, "created": time.time()}, time.time())
    for qid in d.get("remove_questions", []) + d.get("ack_answers", []):
        _delete("q:", "questions", qid)
    return jsonify(ok=True)
