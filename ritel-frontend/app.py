"""
EDEE frontend — Streamlit.

Submit a math word problem, watch the agents' chain of thought live, review the
scaffolding diagram when the pipeline pauses for you, and browse the results.

Run against GCP (default):   streamlit run app.py
Run offline on dummy data:   streamlit run app.py -- --demo
    (replays a real transcript from demo/, simulates the human-review gate, no
     credentials needed — see DemoBackend)

Environment: N8N_START_URL, API_KEY, STATE_MANAGER_URL, FIRESTORE_KEY_JSON (optional)
"""
import html
import io
import json
import os
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import requests
import streamlit as st

# --- CONFIGURATION -----------------------------------------------------------
N8N_START_URL = os.environ.get("N8N_START_URL", "https://edee.app.n8n.cloud/webhook/generate-diagram")
API_KEY = os.environ.get("API_KEY", "mathisfun")
STATE_MANAGER_URL = os.environ.get("STATE_MANAGER_URL", "https://ritel-state-manager-194521282716.us-south1.run.app")
GCP_PROJECT = os.environ.get("GCP_PROJECT", "gen-lang-client-0925957935")
FIRESTORE_DB = os.environ.get("FIRESTORE_DB", "dee-data")
BUCKET_NAME = os.environ.get("EDEE_BUCKET", "edee-job-archives-0925957935")

DEMO = "--demo" in sys.argv or os.environ.get("EDEE_DEMO", "").lower() in ("1", "true", "yes")

# Streamlit 1.50 replaced the "fill the parent container" flags with a width argument
# (buttons previously took use_container_width, images use_column_width).
# requirements.txt pins >=1.50, but a local checkout often has an older build, so
# resolve the right kwargs once here instead of crashing on the first widget.
try:
    _ST = tuple(int(p) for p in st.__version__.split(".")[:2])
except (ValueError, AttributeError):
    _ST = (1, 50)
BTN_FILL = {"width": "stretch"} if _ST >= (1, 50) else {"use_container_width": True}
IMG_FILL = {"width": "stretch"} if _ST >= (1, 50) else {"use_column_width": True}

POLL_SECONDS = 1.5
IDLE_TIMEOUT = 240           # seconds without a state update before we assume the run died
HUMAN_GRACE_SECONDS = 90     # extra slack past a human-review deadline before the idle rule applies
TERMINAL = ("completed", "failed")
HERE = os.path.dirname(os.path.abspath(__file__))

STYLE_OPTIONS = ["casual_mobile", "storybook", "cel_shaded_anime", "claymation_diorama", "mid_century_modern"]
REVIEW_MODES = {
    "automatic": ("Automatic", "Never pause. The pipeline paints over the scaffolding as soon as the inspectors approve it."),
    "timeout": ("Ask me, 30 s window", "After the inspectors approve the scaffolding you get 30 seconds to click Give corrections; otherwise it proceeds."),
    "wait": ("Wait for me", "Pause on the approved scaffolding until you answer (moves on by itself if you're away for 10 minutes)."),
}

# --- AGENT PRESENTATION -------------------------------------------------------
# (emoji, accent colour, stage). Colours are grouped by role: managers purple,
# planners blue/teal, coder green, reviewers amber, inspectors orange, artists pink,
# reporting indigo, errors red, the human gold.
AGENTS = {
    "problem_validation":   ("🔍", "#3b82f6", 1), "image_description": ("💡", "#0ea5e9", 1),
    "image_detail_planner": ("🧭", "#8b5cf6", 2), "dimension_expert": ("📏", "#06b6d4", 2),
    "layout_expert":        ("🧩", "#14b8a6", 2), "visual_director": ("🎬", "#0284c7", 2),
    "markup_specialist":    ("🏷️", "#2563eb", 2), "educator": ("🎓", "#0891b2", 2),
    "3d_specialist":        ("🧊", "#0d9488", 2), "data_viz_expert": ("📊", "#1d4ed8", 2),
    "arrangement_planner":  ("🗂️", "#0369a1", 2), "artistic_planner": ("🎨", "#7c3aed", 2),
    "selector":             ("🔀", "#a855f7", 3), "scaffolding_manager": ("📋", "#9333ea", 3),
    "scaffolding_designer": ("📐", "#6366f1", 3), "coder": ("💻", "#16a34a", 3),
    "reviewer":             ("🧐", "#d97706", 3), "review_manager": ("⚖️", "#b45309", 3),
    "scaffolding_generator": ("🖨️", "#64748b", 3),
    "inspector":            ("🔎", "#ea580c", 3), "inspection_manager": ("🛡️", "#c2410c", 3),
    "product_scout":        ("🛒", "#db2777", 4), "product_designer": ("📦", "#be185d", 4),
    "artist":               ("🖌️", "#ec4899", 4),
    "image_verifier":       ("✅", "#f97316", 5), "image_verifier3": ("✅", "#f97316", 5),
    "issue_aggregator":     ("🧮", "#7c2d12", 5),
    "final_reporter":       ("📣", "#4f46e5", 6),
    "error_handler":        ("🚑", "#dc2626", 0), "error_expert": ("🩺", "#b91c1c", 0),
    "error_injector":       ("⚠️", "#991b1b", 0), "n8n_engine": ("⚙️", "#6b7280", 0),
    "user":                 ("🙋", "#ca8a04", 3), "human_gate": ("🙋", "#a16207", 3),
    "system":               ("🤖", "#6b7280", 0),
}
STAGE_NAMES = {1: "Validate & plan", 2: "Refine description", 3: "Scaffolding", 4: "Illustration", 5: "Review", 6: "Report"}
HUMAN_TASKS = ("human_review_open", "human_review_reply")
FALLBACK_COLOURS = ["#2563eb", "#7c3aed", "#059669", "#d97706", "#db2777", "#0891b2"]


def agent_style(agent_id):
    if agent_id in AGENTS:
        return AGENTS[agent_id]
    return ("🤖", FALLBACK_COLOURS[hash(agent_id) % len(FALLBACK_COLOURS)], 0)


def pretty(agent_id):
    return str(agent_id).replace("_", " ").title().replace("3D", "3D")


# --- BACKENDS -----------------------------------------------------------------
class GcpBackend:
    """Firestore job doc + GCS bucket + n8n webhook + state-manager relay."""

    def __init__(self):
        from google.cloud import firestore, storage
        from google.oauth2 import service_account
        self._fs = firestore
        if "FIRESTORE_KEY_JSON" in os.environ:
            key = json.loads(os.environ["FIRESTORE_KEY_JSON"])
            creds = service_account.Credentials.from_service_account_info(key)
            project = key.get("project_id", GCP_PROJECT)
            self.db = firestore.Client(credentials=creds, project=project, database=FIRESTORE_DB)
            client = storage.Client(credentials=creds, project=project)
        else:
            self.db = firestore.Client(project=GCP_PROJECT, database=FIRESTORE_DB)
            client = storage.Client(project=GCP_PROJECT)
        self.bucket = client.bucket(BUCKET_NAME)

    def get_doc(self, job_id):
        snap = self.db.collection("job_states").document(job_id).get()
        return snap.to_dict() if snap.exists else None

    def get_blob(self, path):
        try:
            return self.bucket.blob(path).download_as_bytes()
        except Exception:
            return None

    def start_job(self, payload, env_choice):
        # 7-day TTL stub, written before the pipeline's first broadcast
        try:
            expiry = datetime.now(timezone.utc) + timedelta(days=7)
            self.db.collection("job_states").document(payload["job_id"]).set(
                {"ttl_expiry": expiry, "status": "starting", "original_query": payload["query"]}, merge=True)
        except Exception as e:  # noqa: BLE001
            st.warning(f"Could not set the database TTL: {e}")
        url = N8N_START_URL
        url = url.replace("/webhook/", "/webhook-test/") if env_choice.startswith("Dev") else url.replace("/webhook-test/", "/webhook/")
        r = requests.post(url, json=payload, headers={"x-api-key": API_KEY}, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"n8n returned HTTP {r.status_code}: {r.text[:200]}")

    def human_decision(self, job_id, action, message=""):
        r = requests.post(f"{STATE_MANAGER_URL}/human-decision/{job_id}",
                          json={"action": action, "message": message}, timeout=30)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:  # noqa: BLE001
                detail = r.text[:300]
            raise RuntimeError(detail or f"HTTP {r.status_code}")
        return r.json()


class DemoBackend:
    """Offline stand-in: replays demo/sample_session_events.json into an in-memory job
    doc on a background thread, simulates the human-review gate, and serves the demo
    scaffolding PNG as every image. Same interface as GcpBackend.

    Prompt tricks: put MAXRETRY in the query to see the max-retries rescue gate,
    FAIL to see a failed run."""

    DELAY = float(os.environ.get("EDEE_DEMO_DELAY", "0.35"))

    def __init__(self):
        with open(os.path.join(HERE, "demo", "sample_session_events.json"), encoding="utf-8") as f:
            self.events = json.load(f)
        with open(os.path.join(HERE, "demo", "sample_scaffolding.png"), "rb") as f:
            self.scaffold = f.read()
        self.final = self._make_final(self.scaffold)
        self.docs, self.images, self.gates = {}, {}, {}
        self.lock = threading.Lock()

    @staticmethod
    def _make_final(scaffold_png):
        try:
            from PIL import Image, ImageDraw, ImageFilter
            base = Image.open(io.BytesIO(scaffold_png)).convert("RGBA")
            w, h = base.size
            bg = Image.new("RGBA", (w, h))
            px = bg.load()
            for y in range(h):
                for x in range(w):
                    px[x, y] = (int(200 + 40 * x / w), int(230 - 30 * y / h), int(180 + 60 * y / h), 255)
            blurred = base.filter(ImageFilter.GaussianBlur(1.2))
            out = Image.alpha_composite(bg, blurred)
            ImageDraw.Draw(out).text((12, 12), "DEMO — stand-in for the painted illustration", fill=(30, 40, 60, 255))
            buf = io.BytesIO()
            out.convert("RGB").save(buf, "PNG")
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return scaffold_png

    # -- interface --------------------------------------------------------------
    def get_doc(self, job_id):
        with self.lock:
            d = self.docs.get(job_id)
            return json.loads(json.dumps(d)) if d else None

    def get_blob(self, path):
        job_id, _, name = path.partition("/")
        return self.images.get(job_id, {}).get(name)

    def start_job(self, payload, env_choice):
        job_id = payload["job_id"]
        with self.lock:
            self.docs[job_id] = {"job_id": job_id, "status": "starting", "original_query": payload["query"],
                                 "timestamp": _now_iso(), "session_events_incremental": [], "total_cost": 0.0}
            self.images[job_id] = {}
            self.gates[job_id] = {"event": threading.Event(), "decision": None}
        threading.Thread(target=self._simulate, args=(job_id, payload), daemon=True).start()

    def human_decision(self, job_id, action, message=""):
        with self.lock:
            doc = self.docs.get(job_id)
            gate = self.gates.get(job_id)
            if not doc or not gate:
                raise RuntimeError("Unknown job")
            hr = doc.get("human_review") or {}
            if doc.get("status") != "awaiting_human" or hr.get("stage") in (None, "closed", "resuming"):
                raise RuntimeError("No open human-review gate for this job (it may have timed out or already been answered)")
            if action == "corrections" and message:
                action = "message"
            gate["decision"] = (action, message)
            doc["status"] = "running"
            doc["human_review"] = {**hr, "stage": "resuming", "last_action": action}
            doc["timestamp"] = _now_iso()
        gate["event"].set()
        return {"status": "resumed", "action": action}

    # -- simulation ---------------------------------------------------------------
    def _update(self, job_id, **fields):
        with self.lock:
            self.docs[job_id].update(fields)
            self.docs[job_id]["timestamp"] = _now_iso()

    def _push(self, job_id, ev, agent_hint=None):
        # Like node 1/3's broadcasts: agent_id is the agent RUNNING the task, so a
        # prompt event (author system/user) reports the agent it was sent to.
        ev = dict(ev, timestamp=_now_iso())
        with self.lock:
            d = self.docs[job_id]
            d["session_events_incremental"].append(ev)
            d["total_cost"] = round(d.get("total_cost", 0) + (ev.get("cost") or 0), 6)
            d["agent_id"] = agent_hint or (ev["author"] if ev["author"] not in ("system", "user") else d.get("agent_id"))
            d["task_id"] = ev.get("task")
            d["timestamp"] = ev["timestamp"]

    def _wait_for_human(self, job_id, reason, mode, stage="gate", round_=0, last_reply=None, confirmed=False):
        wait = 30 if (mode == "timeout" and reason == "review" and stage == "gate") else 600
        deadline = datetime.now(timezone.utc) + timedelta(seconds=wait)
        gate = self.gates[job_id]
        gate["event"].clear()
        gate["decision"] = None
        self._update(job_id, status="awaiting_human", human_review={
            "stage": stage, "reason": reason, "mode": mode, "resume_url": "demo://resume",
            "deadline": deadline.isoformat(), "wait_seconds": wait, "round": round_,
            "last_reply": last_reply, "understanding_confirmed": confirmed, "opened_at": _now_iso()})
        gate["event"].wait(timeout=wait)
        decision = gate["decision"] or ("timeout", "")
        self._update(job_id, status="running", human_review={"stage": "closed", "reason": reason, "last_action": decision[0]})
        return decision

    def _note(self, job_id, text):
        self._push(job_id, {"author": "system", "task": "log_human_decision", "status": "ok", "parts": [{"text": "Record the human reviewer's decision into history."}]})
        self._push(job_id, {"author": "human_gate", "task": "log_human_decision", "status": "ok", "parts": [{"text": text}], "model": "none", "cost": 0})

    def _converse(self, job_id, reason, mode, first_message):
        """Fake the multi-turn manager conversation. Returns 'rework' | 'accept' | 'abort' | 'timeout'."""
        message, round_ = first_message, 0
        while True:
            if message:
                round_ += 1
                task = "human_review_open" if round_ == 1 else "human_review_reply"
                # Text only: the real pipeline attaches no image to the human's turn
                # (the human is the one looking at the render, in this very UI).
                self._push(job_id, {"author": "user", "task": task, "status": "ok",
                                    "parts": [{"text": message}]},
                           agent_hint="inspection_manager")
                time.sleep(max(self.DELAY * 3, 1.0))
                happy = any(w in message.lower() for w in ("fine", "good", "ok as is", "looks good", "no changes"))
                confirmed = happy or round_ >= 2
                reply = {
                    "reasoning": "Demo manager: parsing the human's request against the scaffold.",
                    "reply_to_human": ("Understood — the scaffold stays as it is. Handing it to the artist now." if happy else
                                       f"Got it. To be sure I understand: you want {message.strip().rstrip('.')}. "
                                       + ("Should the labels move with the shapes, or stay where they are?" if round_ == 1 else
                                          "I'll have the coding team redraw it with exactly those changes.")),
                    "understanding_confirmed": confirmed,
                    "scaffold_acceptable_as_is": happy,
                    "fix_instructions": "" if happy else f"Human corrections (round {round_}): {message.strip()}",
                }
                self._push(job_id, {"author": "inspection_manager", "task": task, "status": "ok",
                                    "parts": [{"text": json.dumps(reply)}], "model": "gemini-3.1-pro-preview", "cost": 0.0131})
                if confirmed:
                    return "accept" if happy else "rework"
                action, message = self._wait_for_human(job_id, reason, mode, "conversation", round_, reply["reply_to_human"])
            else:
                action, message = self._wait_for_human(job_id, reason, mode, "conversation", round_)
            if action == "abort":
                return "abort"
            if action in ("continue", "timeout"):
                return "rework" if round_ else ("accept" if action == "continue" else "timeout")
            # action == "message": loop with the new message

    def _simulate(self, job_id, payload):
        mode = payload.get("human_review_mode", "automatic")
        q = payload.get("query", "").upper()
        want_max_retries, want_fail = "MAXRETRY" in q, "FAIL" in q
        gate_done = False
        for i, ev in enumerate(self.events):
            time.sleep(self.DELAY)
            if ev["task"] == "verify_adherence" and ev["author"] == "system":
                self.images[job_id]["latest.png"] = self.scaffold
                self._push(job_id, {"author": "scaffolding_generator", "task": "render", "status": "ok",
                                    "parts": [{"text": "Rendered the scaffolding (see image panel)."}], "model": "none", "cost": 0})
            if want_fail and ev["task"] == "plan_finishing":
                self._push(job_id, {"author": "error_injector", "task": "log_error", "status": "ok", "model": "none", "cost": 0,
                                    "parts": [{"text": "While attempting to execute python code:\n*** PYTHON RUNTIME ERROR ***\nNameError: name 'undefined_var' is not defined\n  line 12: ax.plot([0, 1], [0, undefined_var])"}]})
                self._push(job_id, {"author": "error_handler", "task": "report_error", "status": "ok", "model": "gemini-3.7-flash", "cost": 0.004,
                                    "parts": [{"text": json.dumps({"reasoning": "Demo failure.", "error_message": "We couldn't quite figure out the geometry for this problem after several tries. Try simplifying the illustration request."})}]})
                self._update(job_id, status="failed", error_message="We couldn't quite figure out the geometry for this problem after several tries. Try simplifying the illustration request.")
                return
            nxt = self.events[i + 1] if i + 1 < len(self.events) else None
            self._push(job_id, ev, agent_hint=nxt["author"] if (ev["author"] == "system" and nxt) else None)
            if ev["author"] == "inspection_manager" and ev["task"] == "consolidate_inspection" and not gate_done and mode != "automatic":
                verdict = json.loads(ev["parts"][0]["text"])
                reason = None
                if want_max_retries and not verdict.get("passed_inspection"):
                    reason = "max_retries"
                elif verdict.get("passed_inspection"):
                    reason = "review"
                if reason:
                    gate_done = True
                    self.images[job_id]["scaffolding.png"] = self.scaffold
                    action, message = self._wait_for_human(job_id, reason, mode)
                    outcome = action
                    if action in ("corrections", "message"):
                        outcome = self._converse(job_id, reason, mode, message)
                    if outcome == "abort":
                        self._note(job_id, "The human reviewer chose to stop the run at the scaffolding review.")
                        self._update(job_id, status="failed", error_message="You stopped the run at the scaffolding review.")
                        return
                    if outcome == "timeout" and reason == "max_retries":
                        self._update(job_id, status="failed", error_message="The scaffolding failed inspection three times and nobody answered the review prompt.")
                        return
                    notes = {"continue": "The human reviewer accepted the scaffolding as drawn.",
                             "timeout": "The human reviewer did not respond in time; proceeding automatically.",
                             "accept": "The human reviewer confirmed the scaffolding is fine as drawn.",
                             "rework": "The human reviewer gave corrections; retries reset, redrawing the scaffolding."}
                    self._note(job_id, notes.get(outcome, outcome))
            if ev["author"] == "artist" and ev["task"] == "render_final":
                self.images[job_id]["latest.png"] = self.final
        self.images[job_id]["final_illustration.png"] = self.final
        self.images[job_id]["scaffolding.png"] = self.scaffold
        self.images[job_id].pop("latest.png", None)
        doc = self.get_doc(job_id)
        ss = {}
        for ev in reversed(doc["session_events_incremental"]):
            if ev["author"] == "final_reporter":
                try:
                    ss.update(json.loads(ev["parts"][0]["text"]))
                except Exception:  # noqa: BLE001
                    pass
        self._update(job_id, status="completed", user_message=ss.get("user_message", "Done!"),
                     archival_report=ss.get("archival_report", ""), generation_successful=True)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@st.cache_resource
def get_backend():
    return DemoBackend() if DEMO else GcpBackend()


# --- PAGE SETUP -----------------------------------------------------------------
st.set_page_config(page_title="EDEE Gen", layout="wide", page_icon="📐", initial_sidebar_state="auto")

for key, default in [("job_id", None), ("is_running", False), ("trigger_job", False), ("carousel_idx", 1),
                     ("pending_action", None), ("last_gate_sig", None), ("job_started_at", None), ("notice", None)]:
    st.session_state.setdefault(key, default)

st.markdown("""
<style>
  .block-container { padding-top: 2.4rem !important; padding-bottom: 1rem; }
  .edee-title { font-size: 1.9rem; font-weight: 700; letter-spacing: -0.01em; margin: 0; }
  .edee-sub { opacity: .7; margin: 0 0 .6rem 0; font-size: .95rem; }
  .pill { display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem; font-weight: 600; letter-spacing:.02em; }
  .pill.idle { background: rgba(107,114,128,.18); }
  .pill.running { background: rgba(37,99,235,.18); color:#2563eb; }
  .pill.awaiting_human { background: rgba(202,138,4,.2); color:#b45309; }
  .pill.completed { background: rgba(22,163,74,.18); color:#15803d; }
  .pill.failed { background: rgba(220,38,38,.18); color:#b91c1c; }
  .pill.starting { background: rgba(37,99,235,.12); color:#2563eb; }
  .stepper { display:flex; gap:4px; margin: 6px 0 10px 0; }
  .step { flex:1; text-align:center; font-size:.72rem; padding:5px 2px; border-radius:6px; background: rgba(120,120,120,.12); opacity:.6; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .step.done { background: rgba(22,163,74,.18); opacity:1; }
  .step.active { background: rgba(37,99,235,.22); opacity:1; font-weight:700; box-shadow: inset 0 0 0 1px rgba(37,99,235,.5); }
  .step.failed { background: rgba(220,38,38,.2); opacity:1; }
  .meta { font-size:.8rem; opacity:.75; }
  .logbox { height: 72vh; min-height: 420px; overflow-y: auto; display:flex; flex-direction: column-reverse;
            border-radius: 10px; border: 1px solid rgba(128,128,128,.25); background: rgba(128,128,128,.06); }
  .loginner { padding: 12px; display:flex; flex-direction:column; gap:10px; }
  .card { border-left: 4px solid var(--c); border-radius: 8px; background: color-mix(in srgb, var(--c) 9%, transparent); padding: 8px 12px 10px 12px; }
  .card.pending { opacity:.85; }
  .card.human { border-left-width: 6px; background: color-mix(in srgb, var(--c) 16%, transparent); }
  .card .hdr { display:flex; flex-wrap:wrap; align-items:center; gap:6px 10px; font-size:.86rem; margin-bottom:6px; }
  .card .who { font-weight:700; color: var(--c); }
  .chip { font-size:.7rem; padding:1px 7px; border-radius:6px; background: rgba(128,128,128,.18); font-family: ui-monospace, monospace; }
  .chip.model { background: rgba(37,99,235,.15); }
  .chip.none { background: rgba(202,138,4,.18); }
  .chip.cost { background: rgba(22,163,74,.15); }
  .chip.time { background: transparent; opacity:.65; }
  .field { margin: 4px 0; font-size:.88rem; line-height:1.45; }
  .field .k { font-weight:600; opacity:.85; margin-right:6px; }
  .field .block { white-space: pre-wrap; word-break: break-word; margin-top:2px; }
  .field pre { white-space: pre-wrap; word-break: break-word; font-size:.78rem; margin: 4px 0; padding: 8px; border-radius:6px; background: rgba(0,0,0,.25); }
  .bool { display:inline-block; padding:1px 8px; border-radius:6px; font-size:.78rem; font-weight:600; }
  .bool.t { background: rgba(22,163,74,.2); color:#15803d; } .bool.f { background: rgba(220,38,38,.2); color:#b91c1c; }
  details.inst { margin: 2px 0 6px 0; font-size:.8rem; }
  details.inst summary { cursor:pointer; opacity:.7; }
  details.inst .block { white-space: pre-wrap; word-break: break-word; opacity:.8; padding: 6px 8px; border-left: 2px dashed rgba(128,128,128,.4); margin-top:4px; max-height: 320px; overflow:auto; }
  .divider { text-align:center; font-size:.75rem; letter-spacing:.08em; text-transform:uppercase; opacity:.6; margin: 6px 0 -2px 0; }
  .thinking::after { content: "…"; animation: dots 1.2s steps(4,end) infinite; }
  @keyframes dots { 0%{content:""} 25%{content:"."} 50%{content:".."} 75%{content:"..."} }
  .bubble { border-radius: 12px; padding: 8px 12px; margin: 6px 0; font-size:.9rem; line-height:1.45; max-width: 95%; }
  .bubble.me { background: rgba(202,138,4,.18); margin-left:auto; }
  .bubble.mgr { background: rgba(194,65,12,.14); }
  .bubble .tag { font-size:.72rem; font-weight:700; opacity:.7; display:block; margin-bottom:2px; }
  .countdown { font-variant-numeric: tabular-nums; font-weight:700; }
  .gatebox { border: 1px solid rgba(202,138,4,.5); border-radius: 10px; padding: 10px 14px; background: rgba(202,138,4,.08); margin-bottom: 8px; }
  @media (max-width: 800px) { .logbox { height: 55vh; } .step { font-size:.62rem; } }
</style>
""", unsafe_allow_html=True)


# --- CALLBACKS ----------------------------------------------------------------------
def start_job_callback():
    st.session_state.is_running = True
    st.session_state.trigger_job = True


def queue_action(action, msg_key=None):
    msg = st.session_state.get(msg_key, "") if msg_key else ""
    st.session_state.pending_action = (action, msg)


def prev_image():
    st.session_state.carousel_idx -= 1


def next_image():
    st.session_state.carousel_idx += 1


def open_job_callback():
    jid = (st.session_state.get("open_job_id") or "").strip()
    if jid:
        st.session_state.job_id = jid
        st.session_state.is_running = True
        st.session_state.job_started_at = time.time()
        st.session_state.carousel_idx = 1
        st.session_state.shown_image = None
        st.session_state.last_gate_sig = None


# --- SIDEBAR -------------------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    if DEMO:
        st.info("**Demo mode** — replaying a recorded run, no GCP. Put `MAXRETRY` or `FAIL` in the problem text to see those paths.", icon="🧪")
    env_choice = st.selectbox("Environment", ["Production", "Development (Test)"])
    style_choice = st.selectbox("Style", STYLE_OPTIONS)
    review_mode = st.radio("Human review of the scaffolding", list(REVIEW_MODES), index=0,
                           format_func=lambda k: REVIEW_MODES[k][0],
                           help="The scaffolding is the mathematically exact base diagram. After the AI inspectors approve it you can look it over and talk to the QA manager before the artist paints over it. If the inspectors give up on it, any mode other than Automatic asks you instead of failing.")
    st.caption(REVIEW_MODES[review_mode][1])
    with st.expander("Speed / cost caps"):
        max_text_tier = st.selectbox("Max text model tier", ["slow", "medium", "fast"], help="Caps the strongest text model any agent may use.")
        max_image_tier = st.selectbox("Max image model tier", ["slow", "medium", "fast"])
    with st.expander("Open an existing job"):
        st.text_input("Job ID", key="open_job_id", placeholder="uuid from a previous run")
        st.button("Open", on_click=open_job_callback, **BTN_FILL)

backend = get_backend()

# --- HEADER --------------------------------------------------------------------------
st.markdown("<div class='edee-title'>📐 EDEE — Educational Diagram Generator</div>"
            "<div class='edee-sub'>Word problem in, mathematically exact illustration out — watch the agents think.</div>",
            unsafe_allow_html=True)

col1, col2 = st.columns([1, 1], gap="large")
with col1:
    user_query = st.text_area("Math problem and/or illustration request", height=140,
                              placeholder="Problem: A farmer's rectangular field is 12 m by 8 m ...\n\nIllustration: (optional) what you'd like drawn")
    st.button("Generate diagram", type="primary", **BTN_FILL,
              disabled=st.session_state.is_running, on_click=start_job_callback)
    status_ui = st.empty()
    result_ui = st.empty()
    stepper_ui = st.empty()
    agent_ui = st.empty()
    human_ui = st.empty()
    countdown_ui = st.empty()
    image_ui = st.empty()
    carousel_ui = st.empty()
    notes_ui = st.empty()
    download_ui = st.empty()
with col2:
    st.markdown("**Agent thought log**")
    log_ui = st.empty()
    if not st.session_state.job_id:
        log_ui.markdown("<div class='logbox'><div class='loginner meta'>The agents' chain of thought will stream here, one card per turn, colour-coded by agent.</div></div>", unsafe_allow_html=True)


# --- HELPERS --------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s), quote=False)


def parse_iso(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def fmt_time(ts):
    d = parse_iso(ts)
    return d.astimezone().strftime("%H:%M:%S") if d else ""


def fmt_duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def sorted_events(state):
    events = list(state.get("session_events_incremental") or [])
    return sorted(events, key=lambda e: e.get("timestamp") or "")


def event_texts(ev):
    return [p.get("text") for p in ev.get("parts", []) if isinstance(p, dict) and p.get("text")]


def has_image(ev):
    return any(isinstance(p, dict) and ("inlineData" in p or "inline_data" in p) for p in ev.get("parts", []))


def try_json(text):
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except (ValueError, TypeError):
        return None


def render_value(key, val):
    """One parsed response field → HTML."""
    label = esc(key.replace("_", " ").capitalize())
    if isinstance(val, bool):
        return f"<div class='field'><span class='k'>{label}</span><span class='bool {'t' if val else 'f'}'>{'✔ yes' if val else '✘ no'}</span></div>"
    if isinstance(val, (int, float)):
        return f"<div class='field'><span class='k'>{label}</span>{esc(val)}</div>"
    if isinstance(val, list):
        items = "".join(f"<li>{esc(v) if not isinstance(v, (dict, list)) else esc(json.dumps(v))}</li>" for v in val)
        return f"<div class='field'><span class='k'>{label}</span><ul style='margin:2px 0 4px 18px'>{items}</ul></div>"
    if isinstance(val, dict):
        return f"<div class='field'><span class='k'>{label}</span><pre>{esc(json.dumps(val, indent=1))}</pre></div>"
    s = str(val)
    if key.endswith("_code") or key == "python_code":
        return f"<div class='field'><span class='k'>{label}</span><pre>{esc(s)}</pre></div>"
    if len(s) <= 90 and "\n" not in s:
        return f"<div class='field'><span class='k'>{label}</span>{esc(s)}</div>"
    return f"<div class='field'><span class='k'>{label}</span><div class='block'>{esc(s)}</div></div>"


def render_response_body(ev):
    texts = event_texts(ev)
    if not texts and has_image(ev):
        return "<div class='field'>🖼️ <i>image generated — see the image panel</i></div>"
    out = []
    for t in texts:
        parsed = try_json(t)
        if parsed:
            if "reply_to_human" in parsed:
                out.append(f"<div class='bubble mgr'><span class='tag'>To you</span>{esc(parsed['reply_to_human'])}</div>")
            for k, v in parsed.items():
                if k == "reply_to_human":
                    continue
                out.append(render_value(k, v))
        else:
            out.append(f"<div class='field'><div class='block'>{esc(t)}</div></div>")
    return "".join(out)


def render_instruction(ev):
    texts = event_texts(ev)
    if not texts:
        return ""
    note = " (image attached)" if has_image(ev) else ""
    return (f"<details class='inst'><summary>📋 Instruction{esc(note)}</summary>"
            f"<div class='block'>{esc(chr(10).join(texts))}</div></details>")


def chips(ev):
    model = ev.get("model")
    parts = []
    if model:
        parts.append(f"<span class='chip model'>{esc(model)}</span>" if model != "none" else "<span class='chip none'>canned</span>")
    cost = ev.get("cost")
    if cost:
        parts.append(f"<span class='chip cost'>${cost:.4f}</span>")
    parts.append(f"<span class='chip time'>{fmt_time(ev.get('timestamp'))}</span>")
    return "".join(parts)


def render_card(agent, task, body, pending=False, human=False, extra_chips=""):
    emoji, colour, _ = agent_style(agent)
    who = "You" if agent == "user" else pretty(agent)
    cls = "card" + (" pending" if pending else "") + (" human" if human else "")
    task_chip = f"<span class='chip'>{esc(task)}</span>" if task else ""
    return (f"<div class='{cls}' style='--c:{colour}'><div class='hdr'><span class='who'>{emoji} {esc(who)}</span>"
            f"{task_chip}{extra_chips}</div>{body}</div>")


def build_log_html(events, running_agent=None):
    """One card per turn: a prompt event merged with the reply that follows it.
    `running_agent` names the agent a still-unanswered final prompt belongs to."""
    cards, i, last_stage = [], 0, None
    while i < len(events):
        ev = events[i]
        author, task = ev.get("author", "system"), ev.get("task", "")
        is_prompt = author in ("system", "user")
        nxt = events[i + 1] if i + 1 < len(events) else None
        reply = nxt if (is_prompt and nxt and nxt.get("author") not in ("system", "user")) else None
        agent = reply["author"] if reply else (author if not is_prompt else (author if author == "user" else "system"))
        stage = agent_style(agent)[2] if agent != "system" else None
        if stage and stage != last_stage:
            cards.append(f"<div class='divider'>Phase {stage} · {STAGE_NAMES.get(stage, '')}</div>")
            last_stage = stage
        if author == "user":
            # the human's own message: its own gold card, then the manager's reply card
            cards.append(render_card("user", task, f"<div class='bubble me'>{esc(chr(10).join(event_texts(ev)))}</div>",
                                     human=True, extra_chips=f"<span class='chip time'>{fmt_time(ev.get('timestamp'))}</span>"))
            if reply:
                cards.append(render_card(reply["author"], task, render_response_body(reply), human=True, extra_chips=chips(reply)))
                i += 2
            else:
                cards.append(render_card("inspection_manager", task, "<div class='field thinking'>reading your message</div>", pending=True))
                i += 1
            continue
        if is_prompt and reply:
            cards.append(render_card(reply["author"], task, render_instruction(ev) + render_response_body(reply), extra_chips=chips(reply)))
            i += 2
        elif is_prompt:
            who = running_agent if (i == len(events) - 1 and running_agent and running_agent != "-") else "system"
            cards.append(render_card(who, task, render_instruction(ev) + "<div class='field thinking'>thinking</div>", pending=True))
            i += 1
        else:
            cards.append(render_card(author, task, render_response_body(ev), extra_chips=chips(ev)))
            i += 1
    return "<div class='logbox'><div class='loginner'>" + "".join(cards) + "</div></div>"


def stage_of(state, events):
    agent = state.get("agent_id")
    stage = agent_style(agent)[2] if agent and agent in AGENTS else 0
    if not stage:
        for ev in reversed(events):
            s = agent_style(ev.get("author"))[2]
            if s:
                stage = s
                break
    return stage


def render_stepper(state, events):
    status = state.get("status")
    cur = 6 if status == "completed" else stage_of(state, events)
    parts = []
    for n, name in STAGE_NAMES.items():
        cls = "step"
        if status == "completed" or n < cur:
            cls += " done"
        elif n == cur:
            cls += " failed" if status == "failed" else " active"
        parts.append(f"<div class='{cls}' title='{esc(name)}'>{n}. {esc(name)}</div>")
    return "<div class='stepper'>" + "".join(parts) + "</div>"


def status_line(state, events):
    status = state.get("status", "starting")
    cost = state.get("total_cost")
    started = parse_iso(events[0].get("timestamp")) if events else None
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() if started else None
    bits = [f"<span class='pill {esc(status)}'>{esc(status.replace('_', ' '))}</span>"]
    if elapsed is not None:
        bits.append(f"<span class='meta'>⏱ {fmt_duration(elapsed)}</span>")
    if cost is not None:
        bits.append(f"<span class='meta'>💸 ${float(cost):.4f}</span>")
    if events:
        bits.append(f"<span class='meta'>🧵 {len(events)} events</span>")
    return "<div style='display:flex;gap:14px;align-items:center;flex-wrap:wrap'>" + "".join(bits) + "</div>"


def conversation_from_events(events):
    turns = []
    for ev in events:
        if ev.get("task") not in HUMAN_TASKS:
            continue
        if ev.get("author") == "user":
            turns.append(("me", "\n".join(event_texts(ev))))
        else:
            for t in event_texts(ev):
                p = try_json(t)
                if p and p.get("reply_to_human"):
                    turns.append(("mgr", p["reply_to_human"]))
    return turns


def gate_signature(hr):
    return f"{hr.get('stage')}|{hr.get('round', 0)}|{hr.get('deadline')}"


def render_human_panel(state, events):
    """Widgets for the review gate. Rendered once per gate state (keys carry the signature)."""
    hr = state.get("human_review") or {}
    sig = gate_signature(hr)
    stage, reason = hr.get("stage"), hr.get("reason", "review")
    with human_ui.container():
        if stage == "resuming":
            st.info("Sending your decision to the pipeline…", icon="⏳")
            return
        if stage == "gate":
            if reason == "max_retries":
                st.markdown("<div class='gatebox'>🛑 <b>The inspectors rejected this scaffolding three times.</b> "
                            "You can accept it as it is, tell the QA manager what to fix, or stop the run.</div>", unsafe_allow_html=True)
            else:
                st.markdown("<div class='gatebox'>🙋 <b>The inspectors approved this scaffolding.</b> "
                            "Anything to change before the artist paints over it? Corrections you give "
                            "outrank every AI in the pipeline.</div>", unsafe_allow_html=True)
            st.text_area("Corrections (optional — sending them opens a conversation with the QA manager)",
                         key=f"msg_{sig}", height=90, placeholder="e.g. The two bags should be side by side, and the labels are too small.")
            b1, b2, b3 = st.columns(3)
            b1.button("✅ Continue" if reason == "review" else "✅ Accept as is", key=f"cont_{sig}",
                      on_click=queue_action, args=("continue",), **BTN_FILL, type="primary",
                      help="Proceed with this scaffolding as drawn.")
            b2.button("✏️ Corrections", key=f"corr_{sig}", on_click=queue_action, args=("corrections", f"msg_{sig}"), **BTN_FILL,
                      help="Open a conversation with the QA manager about what to change.")
            b3.button("🛑 Give up", key=f"abort_{sig}", on_click=queue_action, args=("abort",), **BTN_FILL,
                      help="Stop the run here.")
        elif stage == "conversation":
            st.markdown("<div class='gatebox'>💬 <b>Talking to the QA Inspection Manager.</b> It will ask until it is sure it "
                        "understands, then redraw the scaffolding with your corrections. Every message resets the retry budget.</div>",
                        unsafe_allow_html=True)
            turns = conversation_from_events(events)
            if not turns and not hr.get("last_reply"):
                st.caption("Type what you'd like changed.")
            for who, text in turns[-6:]:
                st.markdown(f"<div class='bubble {who}'><span class='tag'>{'You' if who == 'me' else 'QA manager'}</span>{esc(text)}</div>", unsafe_allow_html=True)
            st.text_area("Your message", key=f"msg_{sig}", height=90)
            b1, b2, b3 = st.columns(3)
            b1.button("📨 Send", key=f"send_{sig}", on_click=queue_action, args=("message", f"msg_{sig}"), **BTN_FILL, type="primary")
            b2.button("👍 Proceed", key=f"done_{sig}", on_click=queue_action, args=("continue",), **BTN_FILL,
                      help="That's all — go ahead with what the manager understood so far.")
            b3.button("🛑 Give up", key=f"abort_{sig}", on_click=queue_action, args=("abort",), **BTN_FILL)


def render_countdown(state):
    hr = state.get("human_review") or {}
    deadline = parse_iso(hr.get("deadline"))
    if not deadline or hr.get("stage") not in ("gate", "conversation"):
        countdown_ui.empty()
        return
    left = (deadline - datetime.now(timezone.utc)).total_seconds()
    what = "the run fails" if hr.get("reason") == "max_retries" and hr.get("stage") == "gate" else "the pipeline moves on by itself"
    countdown_ui.markdown(f"<div class='meta'>⏳ <span class='countdown'>{fmt_duration(left)}</span> until {what}.</div>", unsafe_allow_html=True)


def show_image(state):
    """Latest render for the left panel. Fetched only when the pipeline could have
    produced a new one (a render/artist turn) or nothing is shown yet."""
    job_id = state.get("job_id") or st.session_state.job_id
    path = f"{job_id}/final_illustration.png" if state.get("status") == "completed" else f"{job_id}/latest.png"
    agent = state.get("agent_id")
    shown = st.session_state.get("shown_image")
    if shown and shown[0] == job_id and agent not in ("scaffolding_generator", "artist", "-", None) and state.get("status") != "completed":
        return
    data = backend.get_blob(path)
    if data and (not shown or shown[1] != hash(data)):
        st.session_state.shown_image = (job_id, hash(data))
        image_ui.image(data, **IMG_FILL, caption="Latest render" if state.get("status") != "completed" else None)


def draw_state(state):
    events = sorted_events(state)
    status_ui.markdown(status_line(state, events), unsafe_allow_html=True)
    stepper_ui.markdown(render_stepper(state, events), unsafe_allow_html=True)
    status = state.get("status")
    if status not in TERMINAL:
        agent, task = state.get("agent_id"), state.get("task_id")
        if agent and agent != "-":
            emoji, colour, _ = agent_style(agent)
            agent_ui.markdown(f"<span style='color:{colour};font-weight:600'>{emoji} {esc(pretty(agent))}</span> "
                              f"<span class='chip'>{esc(task or '')}</span>", unsafe_allow_html=True)
    if status == "awaiting_human":
        hr = state.get("human_review") or {}
        sig = gate_signature(hr)
        if sig != st.session_state.last_gate_sig:
            st.session_state.last_gate_sig = sig
            render_human_panel(state, events)
        render_countdown(state)
    else:
        if st.session_state.last_gate_sig is not None:
            st.session_state.last_gate_sig = None
            human_ui.empty()
        countdown_ui.empty()
    if status != "completed":
        show_image(state)  # latest render (scaffolding, then the artist's attempts); the carousel takes over on completion
    log_ui.markdown(build_log_html(events, state.get("agent_id")), unsafe_allow_html=True)
    return events


def generate_zip_bundle(state):
    job_id = state.get("job_id") or st.session_state.job_id
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED, False) as zf:
        def add_blob(name, path):
            data = backend.get_blob(path)
            if data:
                zf.writestr(name, data)
            return bool(data)
        add_blob("scaffolding.png", f"{job_id}/scaffolding.png")
        if not add_blob("final_illustration.png", f"{job_id}/final_illustration.png"):
            add_blob("latest.png", f"{job_id}/latest.png")
        if state.get("archival_report"):
            zf.writestr("archival_report.txt", str(state["archival_report"]))
        transcript = sorted_events(state)
        if transcript:
            zf.writestr("session_events.json", json.dumps(transcript, indent=2, default=str))
        meta = {k: v for k, v in state.items() if k not in ("session_events_incremental", "archival_report", "ttl_expiry")}
        zf.writestr("metadata.json", json.dumps(meta, indent=2, default=str))
    return buf.getvalue()


# --- PENDING HUMAN DECISION (queued by a button callback on the previous run) ---------
if st.session_state.pending_action and st.session_state.job_id:
    action, msg = st.session_state.pending_action
    st.session_state.pending_action = None
    try:
        backend.human_decision(st.session_state.job_id, action, msg)
        st.session_state.notice = {"continue": "Continuing.", "corrections": "Opening the conversation…",
                                   "message": "Message sent.", "abort": "Stopping the run."}.get(action, "Sent.")
        st.session_state.last_gate_sig = None
        human_ui.empty()
    except Exception as e:  # noqa: BLE001
        st.session_state.notice = f"Could not send your decision: {e}"
if st.session_state.notice:
    st.toast(st.session_state.notice)
    st.session_state.notice = None

# --- START A JOB --------------------------------------------------------------------------
if st.session_state.trigger_job:
    st.session_state.trigger_job = False
    if not user_query.strip():
        st.session_state.is_running = False
        st.warning("Please enter a math problem or illustration request first.")
        st.stop()
    st.session_state.job_id = str(uuid.uuid4())
    st.session_state.job_started_at = time.time()
    st.session_state.carousel_idx = 1
    st.session_state.last_gate_sig = None
    st.session_state.shown_image = None
    payload = {"query": user_query, "job_id": st.session_state.job_id, "style_preference": style_choice,
               "human_review_mode": review_mode, "max_text_tier": max_text_tier, "max_image_tier": max_image_tier}
    try:
        backend.start_job(payload, env_choice)
    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to start the pipeline: {e}")
        st.session_state.is_running = False
        st.session_state.job_id = None
        st.stop()

# --- POLL / RENDER ---------------------------------------------------------------------------
if st.session_state.job_id:
    job_id = st.session_state.job_id
    st.caption(f"Job `{job_id}`")
    try:
        if st.session_state.is_running:
            last_ts, last_update = None, time.time()
            status_ui.info("⏳ Pipeline initiated. Listening for agents…")
            while st.session_state.is_running:
                state = backend.get_doc(job_id)
                now = time.time()
                if state and state.get("timestamp") != last_ts:
                    last_ts, last_update = state.get("timestamp"), now
                    draw_state(state)
                    if state.get("status") in TERMINAL:
                        st.session_state.is_running = False
                        st.rerun()
                elif state and state.get("status") == "awaiting_human":
                    render_countdown(state)
                # idle watchdog: a human gate is allowed to sit until its deadline
                limit = IDLE_TIMEOUT
                if state and state.get("status") == "awaiting_human":
                    dl = parse_iso((state.get("human_review") or {}).get("deadline"))
                    if dl:
                        limit = max(limit, (dl - datetime.now(timezone.utc)).total_seconds() + HUMAN_GRACE_SECONDS)
                if now - last_update > limit:
                    result_ui.error(f"⌛ No update from the pipeline for {int(now - last_update)} s. It may have crashed — "
                                    "the log shows how far it got. Use 'Open an existing job' later to check again.")
                    st.session_state.is_running = False
                    break
                time.sleep(POLL_SECONDS)
        else:
            state = backend.get_doc(job_id)
            if not state:
                status_ui.warning("No record of that job (it may have expired).")
            else:
                events = draw_state(state)
                status = state.get("status")
                if status == "completed":
                    result_ui.success("✅ Generation complete!")
                    images = [(f"{job_id}/scaffolding.png", "Scaffolding blueprint (Phase 3)"),
                              (f"{job_id}/final_illustration.png", "Final illustration (Phase 4–5)")]
                    st.session_state.carousel_idx = max(0, min(st.session_state.carousel_idx, len(images) - 1))
                    path, label = images[st.session_state.carousel_idx]
                    data = backend.get_blob(path)
                    if data:
                        image_ui.image(data, caption=label, **IMG_FILL)
                    else:
                        image_ui.error("Image not found in storage.")
                    with carousel_ui.container():
                        c1, c2, c3, c4 = st.columns([3, 1, 1, 3])
                        c2.button("❮", on_click=prev_image, disabled=st.session_state.carousel_idx == 0, **BTN_FILL)
                        c3.button("❯", on_click=next_image, disabled=st.session_state.carousel_idx == len(images) - 1, **BTN_FILL)
                    if state.get("user_message"):
                        with notes_ui.container():
                            st.markdown("**📝 Notes from the pipeline**")
                            st.info(state["user_message"])
                elif status == "failed":
                    result_ui.error(f"❌ Failed: {state.get('error_message') or 'no explanation was recorded'}")
                    show_image(state)
                else:
                    result_ui.info("This job is still running elsewhere — reconnecting…")
                    show_image(state)
                    st.session_state.is_running = True
                    st.rerun()
                if status in TERMINAL:
                    download_ui.download_button("📦 Download results (ZIP)", generate_zip_bundle(state),
                                                file_name=f"EDEE_{job_id}.zip", mime="application/zip", **BTN_FILL)
    except Exception as e:  # noqa: BLE001
        st.error(f"Error during polling/rendering: {e}")
        st.session_state.is_running = False
