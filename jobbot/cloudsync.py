"""Laptop side of the cloud inbox (cloud/ on Vercel). While the local app runs it:
- pulls links pasted on the phone and processes them strictly in the order they were added,
- applies approvals / rejections made on the phone (oldest first),
- relays form questions to the phone and answers back,
- pushes every job's status so the phone shows what's happening.
Links added while the laptop is off simply wait in the cloud until the next run."""
import hashlib
import json
import os
import queue
import threading
import time

import httpx

from . import db, formfill, pipeline
from .config import ROOT, cfg

MAP = ROOT / "data" / "cloud_map.json"
TOKEN_FILE = ROOT / "profile" / "cloud_token"
VISIBLE = ("queued", "approved", "filling", "awaiting_input", "filled", "submitted", "needs_manual", "rejected",
           "applied_manual", "error", "cancelled")
ACTIONS = ("approved", "rejected", "applied_manual", "submitted", "needs_manual", "cancelled")
CLOUD_SKIP = "__skip__"


def settings():
    c = cfg.get("cloud") or {}
    token = os.environ.get("JOBBOT_AGENT_TOKEN") or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else "")
    return (c.get("url") or "").rstrip("/"), token


class Sync:
    def __init__(self, url, token):
        self.url, self.h = url, {"Authorization": f"Bearer {token}"}
        self.lock = threading.Lock()
        try:
            self.m = json.loads(MAP.read_text())
        except FileNotFoundError:
            self.m = {}
        for k in ("item_to_job", "pushed", "qs"):
            self.m.setdefault(k, {})
        self.ver, self.todo, self.seen = None, queue.Queue(), set()
        self.last_error = None

    def _post(self, path, body):
        r = httpx.post(self.url + path, headers=self.h, json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def _save(self):
        with self.lock:
            MAP.parent.mkdir(exist_ok=True)
            MAP.write_text(json.dumps(self.m))

    def cloud_id(self, job_id):
        with self.lock:
            for k, v in self.m["item_to_job"].items():
                if v == job_id:
                    return k
            cid = "s" + hashlib.md5(job_id.encode()).hexdigest()[:11]
            self.m["item_to_job"][cid] = job_id
            return cid

    # -- links from the phone: one processor thread, strictly first-in first-out --
    def processor(self):
        while True:
            it = self.todo.get()
            try:
                self._post("/api/agent/push", {"items": [{"id": it["id"], "status": "processing"}]})
                jid, msg = pipeline.process_link(it["url"], it.get("pasted", ""), say=lambda m: None)
                with self.lock:
                    self.m["item_to_job"][it["id"]] = jid
                    self.m["pushed"].pop(it["id"], None)
                self._save()
                job = db.get(jid)
                if (cfg.get("cloud") or {}).get("auto_approve_pasted") and job["status"] == "queued" \
                        and job["source"] != "manual":
                    db.update(jid, status="approved")
                    formfill.enqueue(jid)
                if msg.startswith("Already"):
                    self._post("/api/agent/push", {"items": [{"id": it["id"], "status": job["status"], "notes": msg}]})
            except (Exception, SystemExit) as e:
                try:
                    self._post("/api/agent/push", {"items": [{"id": it["id"], "status": "error", "notes": str(e)[:300]}]})
                except Exception:
                    pass

    def sync_once(self):
        d = self._post("/api/agent/pull", {"ver": self.ver})
        acks, ans_acks = [], []
        if not d.get("unchanged"):
            for it in sorted(d["waiting"], key=lambda i: i["created"]):
                if it["id"] not in self.seen and it["id"] not in self.m["item_to_job"]:
                    self.seen.add(it["id"])
                    self.todo.put(it)
            for a in sorted(d["actions"], key=lambda i: i["created"]):  # oldest job first
                jid = self.m["item_to_job"].get(a["id"])
                if jid and db.get(jid) and a["action"] in ACTIONS:
                    db.update(jid, status=a["action"])
                    if a["action"] == "approved":
                        formfill.enqueue(jid)
                acks.append({"id": a["id"], "action": a["action"]})
            for a in d["answers"]:
                lq = self.m["qs"].pop(a["id"], None)
                if lq is not None and db.get_question(lq)["answer"] is None:
                    ans = formfill.SKIP if a["answer"] == CLOUD_SKIP else a["answer"]
                    db.answer_question(lq, ans, a.get("remember", True) and ans != formfill.SKIP)
                ans_acks.append(a["id"])
        # statuses (only what changed since the last push)
        items, hashes = [], {}
        for j in db.by_statuses(VISIBLE):
            r = json.loads(j["resume_json"]) if j["resume_json"] else {}
            summary = "\n".join([r.get("summary", "")] + [f"- {b}" for e in r.get("experience", []) for b in e["bullets"]]
                                + [f"- {p['name']}: {b}" for p in r.get("projects", []) for b in p["bullets"]]).strip()
            snap = dict(status=j["status"], title=j["title"], company=j["company"], location=j["location"],
                        score=j["score"], salary_lpa=j["salary_lpa"], source=j["source"], url=j["url"],
                        notes=j["notes"] or "", summary=summary, cover=j["cover"] or "",
                        warnings=json.loads(j["warnings"] or "[]"))
            cid = self.cloud_id(j["id"])
            h = hashlib.md5(json.dumps(snap, sort_keys=True, default=str).encode()).hexdigest()
            if self.m["pushed"].get(cid) != h:
                items.append({"id": cid, "created": j["created"], **snap})
                hashes[cid] = h
        # questions waiting for an answer -> phone; answered locally -> remove from phone
        questions, remove = [], []
        for q in db.pending_questions():
            cq = f"q{q['id']}"
            if cq not in self.m["qs"]:
                self.m["qs"][cq] = q["id"]
                questions.append({"id": cq, "item_id": self.cloud_id(q["job_id"]), "label": q["label"], "kind": q["kind"],
                                  "options": json.loads(q["options"] or "[]"), "suggestion": q["suggestion"] or "",
                                  "title": q["title"], "company": q["company"]})
        for cq, lq in list(self.m["qs"].items()):
            if db.get_question(lq)["answer"] is not None:
                remove.append(cq)
                del self.m["qs"][cq]
        if items or questions or remove or acks or ans_acks:
            self._post("/api/agent/push", {"items": items, "questions": questions, "remove_questions": remove,
                                           "ack_actions": acks, "ack_answers": ans_acks})
            self.m["pushed"].update(hashes)
        self._save()
        self.ver = d.get("ver")


_sync = None


def start():
    """Called by the local app. No-op unless cloud.url and the agent token are set."""
    global _sync
    url, token = settings()
    if not (url and token) or _sync:
        return None
    _sync = Sync(url, token)
    threading.Thread(target=_sync.processor, daemon=True).start()

    def loop():
        while True:
            try:
                _sync.sync_once()
                _sync.last_error = None
            except Exception as e:
                _sync.last_error = str(e)[:200]
                _sync.ver = None  # resync fully next time
            time.sleep((cfg.get("cloud") or {}).get("poll_seconds", 30))

    threading.Thread(target=loop, daemon=True).start()
    print(f"Cloud inbox sync on: {url}")
    return _sync
