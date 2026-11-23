"""One page on this laptop: paste a job link, get a tailored PDF."""
import threading
import webbrowser

from flask import Flask, render_template_string, request, send_from_directory

from . import resume

app = Flask(__name__)
PORT = 5000
ASSETS_V = "5"
PAGE = f"""<!doctype html><title>Resume tailor</title><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/glass.css?v={ASSETS_V}"><script src="/static/glass.js?v={ASSETS_V}" defer></script>
""" + """<h2>Tailor my resume</h2>
<form class="box" method="post" onsubmit="const b=this.querySelector('button');b.disabled=true;b.textContent='Tailoring and compiling...'">
 <input type="url" name="url" value="{{ url }}" placeholder="Paste the job link">
 <textarea name="pasted" rows="4" placeholder="Only if the link needs a login: paste the job description here">{{ pasted }}</textarea>
 <button class="ok">Make PDF</button></form>
{% if error %}<div class="warn">{{ error }}</div>{% endif %}
{% if r %}<div class="box"><b>{{ r.role }} - {{ r.company }}</b>
 {% if r.note %}<div class="warn">{{ r.note }}</div>{% endif %}
 <p><a href="/file/{{ r.pdf }}" target="_blank">Open PDF</a> | <a href="/file/{{ r.pdf }}" download>Download PDF</a> |
 <a href="/file/{{ r.tex }}" download>Download LaTeX</a></p></div>{% endif %}"""


@app.route("/", methods=["GET", "POST"])
def home():
    url, pasted = request.form.get("url", ""), request.form.get("pasted", "")
    r, error = None, ""
    if request.method == "POST":
        try:
            r = resume.make(url, pasted)
        except (Exception, SystemExit) as e:
            error = str(e)
    return render_template_string(PAGE, r=r, error=error, url=url, pasted=pasted)


@app.get("/file/<path:name>")
def file(name):
    return send_from_directory(resume.OUTPUT, name)


def run():
    from waitress import serve
    print(f"Resume tailor: http://127.0.0.1:{PORT}  (keep this window open)")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    serve(app, host="127.0.0.1", port=PORT, threads=4, channel_timeout=900)
