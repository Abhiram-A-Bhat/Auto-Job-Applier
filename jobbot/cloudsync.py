"""Takes links from the website in order, tailors them on this laptop, and uploads the PDF back."""
import base64
import os
import queue
import threading
import time
from pathlib import Path

import httpx

from . import resume
from .config import ROOT, cfg

TOKEN_FILE = ROOT / "profile" / "cloud_token"


class Sync:
    def __init__(self, url, token):
        self.url, self.h = url.rstrip("/"), {"Authorization": f"Bearer {token}"}
        self.todo, self.taken = queue.Queue(), set()

    def post(self, path, body):
        r = httpx.post(self.url + path, headers=self.h, json=body, timeout=60)
        r.raise_for_status()
        return r.json()

    def send(self, path, body):
        """Keeps trying through internet drops, so a finished PDF is never lost."""
        while True:
            try:
                return self.post(path, body)
            except httpx.TransportError:
                time.sleep(20)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:  # removed on the website
                    return None
                if e.response.status_code < 500:
                    raise
                time.sleep(20)

    def reachable(self):
        try:
            httpx.get(self.url + "/login", timeout=10)
            return True
        except httpx.TransportError:
            return False

    def handle(self, it):
        if self.send("/api/agent/status", {"id": it["id"], "status": "processing"}) is None:
            return
        while True:
            try:
                r = resume.make(it["url"], it.get("pasted", ""))
                break
            except (Exception, SystemExit) as e:
                if self.reachable():  # a real problem with this job, not your internet
                    self.send("/api/agent/status", {"id": it["id"], "status": "error", "error": str(e)})
                    return
                while not self.reachable():
                    time.sleep(20)
        pdf, tex = resume.OUTPUT / r["pdf"], resume.OUTPUT / r["tex"]
        self.send("/api/agent/upload", {"id": it["id"], "pdf": base64.b64encode(pdf.read_bytes()).decode("ascii"),
                                        "tex": tex.read_text(encoding="utf-8"), "role": r["role"],
                                        "company": r["company"], "name": Path(r["pdf"]).stem, "note": r["note"]})

    def worker(self):
        while True:
            it = self.todo.get()  # one at a time, oldest first
            try:
                self.handle(it)
            except Exception as e:
                print(f"Couldn't finish {it['url'][:60]}: {str(e)[:200]}")

    def poll(self):
        ver = None
        while True:
            try:
                d = self.post("/api/agent/pull", {"ver": ver})
                if not d.get("unchanged"):
                    for it in d["todo"]:
                        key = (it["id"], it.get("attempt", 0))
                        if key not in self.taken:
                            self.taken.add(key)
                            self.todo.put(it)
                    ver = d["ver"]
            except Exception:
                ver = None
            time.sleep((cfg.get("cloud") or {}).get("poll_seconds", 15))


def start():
    url = (cfg.get("cloud") or {}).get("url", "")
    token = os.environ.get("JOBBOT_AGENT_TOKEN") or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else "")
    if not (url and token):
        print("Website sync is off (no cloud url or token).")
        return None
    s = Sync(url, token)
    threading.Thread(target=s.worker, daemon=True).start()
    threading.Thread(target=s.poll, daemon=True).start()
    print(f"Connected to {url}")
    return s
