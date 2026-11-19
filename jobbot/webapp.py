"""The interface: paste a link -> tailored resume queued -> approve -> form is filled in a visible browser.
It asks you (here) only for answers it doesn't remember. Nothing is submitted without you."""
import json
import threading
import webbrowser

from flask import Flask, redirect, render_template_string, request, send_file

from . import db, fetchjob, formfill, memory, usage
from .config import load_master
from .pipeline import tailor_job

app = Flask(__name__)
TASKS = []  # in-memory progress of pasted links

CSS = """<style>
body{font:15px system-ui;max-width:900px;margin:24px auto;padding:0 16px;background:#fafafa;color:#222}
details,.box{background:#fff;border:1px solid #ddd;border-radius:8px;margin:10px 0;padding:10px 14px}
summary{cursor:pointer;font-weight:600}.meta{color:#666;font-weight:400}
.warn{background:#fff0f0;border:1px solid #e99;padding:6px 10px;border-radius:6px;margin:8px 0}
.ask{background:#fffbe6;border-color:#e6c200}.note{white-space:pre-wrap;color:#555}
input[type=text],input[type=url],textarea,select{width:100%;padding:6px;box-sizing:border-box;margin:4px 0}
button,a.btn{padding:6px 14px;border-radius:6px;border:1px solid #999;background:#eee;cursor:pointer;
text-decoration:none;color:#000;font-size:14px;display:inline-block}
.ok{background:#d9f2d9}.no{background:#f5d9d9}pre{white-space:pre-wrap;background:#f4f4f4;padding:8px;border-radius:6px}
nav a{margin-right:14px}
</style>"""

PANEL = """
{% for t in tasks %}<div class="box">{{ '⏳' if t.state=='working' else ('✓' if t.state=='done' else '⚠') }}
 <b>{{ t.url[:70] }}</b> <span class="meta">{{ t.msg }}</span></div>{% endfor %}
{% for q in questions %}<form class="box ask" method="post" action="/answer/{{ q.id }}">
 <b>{{ q.label }}</b> <span class="meta">({{ q.title }} - {{ q.company }})</span>
 {% if q.opts %}<select name="answer">{% for o in q.opts %}<option>{{ o }}</option>{% endfor %}</select>
 {% elif q.kind == 'textarea' %}<textarea name="answer" rows="6">{{ q.suggestion }}</textarea>
 <div class="meta">Drafted from your resume - edit before saving.</div>
 {% else %}<input type="text" name="answer" autofocus>{% endif %}
 {% if q.kind != 'textarea' %}<label><input type="checkbox" name="remember" checked> Remember for next time</label><br>{% endif %}
 <button class="ok">Save &amp; continue</button> <button name="skip" value="1" formnovalidate>Leave blank</button></form>{% endfor %}
{% for j in active %}<div class="box"><b>{{ j.title }} - {{ j.company }}</b>
 <span class="meta">{{ {'approved':'waiting for the browser','filling':'filling the form...','awaiting_input':'waiting for your answer above','filled':'FORM FILLED - review the browser window'}[j.status] }}</span>
 {% if j.notes %}<div class="note">{{ j.notes }}</div>{% endif %}
 {% if j.status == 'filled' %}<form method="post" action="/act/{{ j.id }}/submitted" style="display:inline"><button class="ok">I submitted it</button></form>
 <form method="post" action="/act/{{ j.id }}/needs_manual" style="display:inline"><button>Didn't go through</button></form>
 {% else %}<form method="post" action="/act/{{ j.id }}/cancelled" style="display:inline"><button class="no">Cancel</button></form>{% endif %}</div>{% endfor %}"""

QUEUE = """
<h3>Approval queue ({{ jobs|length }}) <span class="meta">{{ counts }}</span></h3>
{% for j in jobs %}<details><summary>{{ j.title }} - {{ j.company }} <span class="meta">| {{ j.location }} | fit {{ j.score if j.score is not none else '-' }} | {{ j.source }}</span></summary>
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

INDEX = """<!doctype html><title>Job applier</title>""" + CSS + """
<nav><a href="/">Apply</a><a href="/memory">Remembered answers</a></nav>
<div id="usage" class="box meta">{{ usage }}</div>
<h2>Paste a job link</h2>
<form class="box" method="post" action="/add">
 <input type="url" name="url" placeholder="https://jobs.lever.co/... / boards.greenhouse.io/... / company careers page" required>
 <textarea name="pasted" rows="3" placeholder="Only needed for LinkedIn / Indeed / Wellfound / Talboss or pages I can't read: paste the job description here"></textarea>
 <button class="ok">Tailor my resume</button></form>
<div id="panel">{{ panel|safe }}</div>
<div id="queue">{{ queue|safe }}</div>
<script>
async function poll(id, url, skip){ const el=document.getElementById(id);
 if(skip(el)) return; try{ el.innerHTML=await (await fetch(url)).text(); }catch(e){} }
setInterval(()=>poll('panel','/panel',el=>el.contains(document.activeElement)&&document.activeElement.tagName!=='BODY'),3000);
setInterval(()=>poll('usage','/usage',()=>false),5000);
setInterval(()=>poll('queue','/queue',el=>el.querySelector('details[open]')),5000);
</script>"""

MEMORY = """<!doctype html><title>Remembered answers</title>""" + CSS + """
<nav><a href="/">Apply</a><a href="/memory">Remembered answers</a></nav>
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


@app.get("/")
def index():
    return render_template_string(INDEX, panel=_panel(), queue=_queue(), usage=usage.text())


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
        master, txt = load_master()
        job = fetchjob.resolve(url, pasted)
        db.insert_job({**job, "status": "processing"})
        if db.get(job["id"])["status"] not in ("processing", "new", "scored", "skipped", "error", "rejected"):
            task.update(state="done", msg=f"Already in the system (status: {db.get(job['id'])['status']}).")
            return
        db.update(job["id"], status="processing", description=job["description"])
        tailor_job(db.get(job["id"]), master, txt)
        task.update(state="done", msg=f"Queued for approval: {job['title']} - {job['company']}")
    except (Exception, SystemExit) as e:
        task.update(state="error", msg=str(e))


@app.post("/add")
def add():
    task = {"url": request.form["url"].strip(), "state": "working", "msg": "reading the job and tailoring your resume..."}
    TASKS.append(task)
    threading.Thread(target=_process, args=(task, task["url"], request.form.get("pasted", "")), daemon=True).start()
    return redirect("/")


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
    for j in db.by_status("approved"):
        formfill.enqueue(j["id"])
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)
