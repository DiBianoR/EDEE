from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from google.cloud import firestore, storage
import os
import json
import base64
import requests
from datetime import datetime, timezone
from google.oauth2 import service_account

app = FastAPI()

# --- INITIALIZATION ---
PROJECT_ID = "gen-lang-client-0925957935"
DB_NAME = "dee-data"
BUCKET_NAME = "edee-job-archives-0925957935"
PLACEHOLDER_BUCKET = "edee-permanent-assets-0925957935"
PLACEHOLDER_BLOB = "no_scaffolding.jpg"

if "FIRESTORE_KEY_JSON" in os.environ:
    key_dict = json.loads(os.environ.get("FIRESTORE_KEY_JSON"))
    credentials = service_account.Credentials.from_service_account_info(key_dict)
    db = firestore.Client(credentials=credentials, project=PROJECT_ID, database=DB_NAME)
    storage_client = storage.Client(credentials=credentials, project=PROJECT_ID)
else:
    db = firestore.Client(project=PROJECT_ID, database=DB_NAME)
    storage_client = storage.Client(project=PROJECT_ID)

bucket = storage_client.bucket(BUCKET_NAME)

@app.post("/update-state")
async def update_state(request: Request):
    try:
        data = await request.json()
        
        job_id = data.get("job_id")
        if not job_id:
            raise HTTPException(status_code=400, detail="Missing job_id in payload")
            
        doc_ref = db.collection("job_states").document(job_id)
        
        # 1. This turn's ADK session events ({author, task, status, parts, timestamp,
        #    [model], [cost]}), shipped one at a time as each becomes available:
        #    node 1 sends the prompt event BEFORE the API call, node 3 the response
        #    event after — so a crashed turn still leaves its prompt in the record.
        events = data.get("events")

        # Legacy query/response payloads: query/response are simply dropped from the
        # doc merge below (no `logs` array is kept anymore).

        # 2. Extract top-level data
        top_level_data = data.copy()
        top_level_data.pop("query", None)
        top_level_data.pop("response", None)

        # 3. ROUTE IMAGES TO THE BUCKET
        status = top_level_data.get("status")
        base64_img = top_level_data.pop("base64_img_string", None)
        mime_type = top_level_data.pop("base64_img_string_mime", "image/png")
        scaff_base64 = top_level_data.pop("scaffolding_base64_img_string", None)
        scaff_mime = top_level_data.pop("scaffolding_base64_img_string_mime", "image/png")
        scaff_placeholder = top_level_data.pop("scaffolding_placeholder", False)

        # Handle the main image (Latest vs Final)
        if base64_img:
            image_data = base64.b64decode(base64_img)
            if status == "completed":
                final_path = f"{job_id}/final_illustration.png"
                blob = bucket.blob(final_path)
                blob.upload_from_string(image_data, content_type=mime_type)

                # Delete the temporary running image to save space
                latest_blob = bucket.blob(f"{job_id}/latest.png")
                if latest_blob.exists():
                    latest_blob.delete()
            else:
                latest_path = f"{job_id}/latest.png"
                blob = bucket.blob(latest_path)
                blob.upload_from_string(image_data, content_type=mime_type)

        # Handle the scaffolding image.
        # Written at the END OF PHASE 3 by the n8n "Archive Scaffolding" node, not on
        # completion: the scaffold is final once inspections pass, and pinning it here
        # means the completion payload no longer has to carry it. Status-agnostic on
        # purpose — do not re-add a `status == "completed"` gate.
        if scaff_base64:
            scaff_data = base64.b64decode(scaff_base64)
            scaff_path = f"{job_id}/scaffolding.png"
            scaff_blob = bucket.blob(scaff_path)
            scaff_blob.upload_from_string(scaff_data, content_type=scaff_mime)
        elif scaff_placeholder:
            # No scaffold this run (DIRECT_IMAGE_GEN, or an upstream skip). Copy the
            # stock filler server-side: the bytes never transit n8n or this process,
            # so the job bucket holds one small object instead of a re-uploaded copy.
            src_bucket = storage_client.bucket(PLACEHOLDER_BUCKET)
            copied = src_bucket.copy_blob(
                src_bucket.blob(PLACEHOLDER_BLOB), bucket, f"{job_id}/scaffolding.png"
            )
            copied.content_type = "image/jpeg"   # cosmetic; .png name, .jpg bytes
            copied.patch()

        # One-shot full transcript from Send Final Broadcast: the FINISHED transcript,
        # destined for the bucket — as distinct from the doc's
        # `session_events_incremental`, accumulated turn by turn. Popped, never stored:
        # the doc already holds every event, and a duplicate would push a long run
        # toward Firestore's 1 MiB doc cap. Used only as a fallback transcript source
        # and debug cross-check in the terminal block below.
        snapshot = top_level_data.pop("session_events", None)

        # Human-in-the-loop gate state, written by the n8n "Human Gate" nodes alongside
        # status "awaiting_human". Replaced WHOLESALE (update, not merge) so a closed
        # gate can't leave a stale resume_url/deadline behind for the frontend to act on.
        # Shape: { stage: gate|conversation|resuming|closed, reason, mode, resume_url,
        #          deadline, wait_seconds, round, last_reply, understanding_confirmed }
        human_review = top_level_data.pop("human_review", None)

        # 4. Append this turn's events to the WORKING transcript,
        #    `session_events_incremental` (exact ADK event shape — the final
        #    session_events.json is materialized from it verbatim).
        #    NOTE: ArrayUnion is a SET union with no ordering guarantee — readers must
        #    sort by timestamp. Byte-identical events collapse, which per-event
        #    timestamps make a non-issue in practice.
        top_level_data.pop("events", None)
        if events:
            top_level_data["session_events_incremental"] = firestore.ArrayUnion(events)

        doc_ref.set(top_level_data, merge=True)
        if human_review is not None:
            doc_ref.update({"human_review": human_review})

        # 5. Terminal status ⇒ materialize the transcript into the bucket as
        #    {job_id}/session_events.json (formerly history.json, renamed with the
        #    ADK conversion — the content is exactly the session_events array),
        #    sourced from the doc's own per-turn accumulated log. Fires once per
        #    run, on "completed" or a clean
        #    "failed". Hard crashes never reach here — nothing arrives at all — so a
        #    crashed run's transcript lives in Firestore only; consumers read the doc.
        #    ArrayUnion guarantees no order, hence the timestamp sort.
        if status in ("completed", "failed"):
            state = doc_ref.get().to_dict() or {}
            # Snapshot first: it's the workflow's own in-memory array, complete and
            # correctly ordered by construction, and it's what this file has always
            # been built from. The incremental log is the fallback — and the ONLY
            # source on a clean "failed" run, where the terminal broadcast comes from
            # node 3 (which sends a single event, never the full snapshot).
            incremental = state.get("session_events_incremental")
            transcript = snapshot or incremental or []
            transcript = sorted(transcript, key=lambda e: e.get("timestamp") or "")

            # Debug cross-check: the one-shot snapshot should exactly match the
            # events we accumulated turn by turn — a mismatch means a broadcast was
            # lost or duplicated, so make it loud in the Cloud Run logs.
            if incremental is not None and snapshot is not None:
                if len(incremental) != len(snapshot):
                    print(f"TRANSCRIPT MISMATCH job {job_id}: "
                          f"incremental={len(incremental)} events, "
                          f"one-shot snapshot={len(snapshot)} events")
            if transcript:
                bucket.blob(f"{job_id}/session_events.json").upload_from_string(
                    json.dumps(transcript, indent=2, default=str),
                    content_type="application/json"
                )

                # Cost, derived from the SAME array that just became
                # session_events.json, so the file and its cost can never disagree.
                # On a completed run that array is the workflow's own snapshot; a
                # clean failure ships no snapshot, so it's the incremental log —
                # `source` records which, since the two are not equally trusted.
                bucket.blob(f"{job_id}/total_cost.json").upload_from_string(
                    json.dumps({
                        "total_cost": round(sum(ev.get("cost") or 0 for ev in transcript), 6),
                        "cost_source": "session_events" if snapshot else "session_events_incremental",
                    }, indent=2),
                    content_type="application/json"
                )

        return {"status": "success", "message": f"Updated job {job_id}"}

    except HTTPException:
        # Deliberate 4xx (e.g. missing job_id) — let it through with its real status
        # and detail instead of the catch-all mangling it into a bare 500. Before this,
        # str(HTTPException) is "" so a blank job_id surfaced as 500 {"detail":""}.
        raise
    except Exception as e:
        print(f"Error updating state: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


# Fields /summary never returns — large enough to defeat the point of a lightweight
# lookup. Use /transcript/{job_id} for those. Legacy names included so a doc written
# by an older build can't blow up a summary response.
BULK_FIELDS = ("session_events_incremental", "session_events", "logs")


@app.get("/summary/{job_id}")
def get_summary(job_id: str):
    """Everything known about a job except the bulk transcript.

    General purpose, not tailored to any one consumer: the Firestore doc verbatim
    minus BULK_FIELDS, plus a manifest of what actually exists in the job's bucket
    folder. Works at any point in a run — including after a hard crash, where the
    doc is the only surviving record.

    BUCKET FIRST, Firestore second. Bucket objects are finalized at terminal status
    and never expire; the job doc carries a 7-day TTL. So an archived job still
    summarizes correctly long after its doc is gone, and a finalized value is never
    shadowed by the running estimate it superseded.
    """
    # Manifest first — it doubles as the existence proof when the doc has expired.
    # One list call beats an exists() per blob, and it reports whatever is actually
    # there rather than only the filenames we thought to ask about.
    artifacts = {
        blob.name.split("/", 1)[-1]: {
            "url": f"https://storage.googleapis.com/{BUCKET_NAME}/{blob.name}",
            "size": blob.size,
            "updated": blob.updated,
        }
        for blob in storage_client.list_blobs(bucket, prefix=f"{job_id}/")
    }

    snap = db.collection("job_states").document(job_id).get()
    state = (snap.to_dict() or {}) if snap.exists else {}
    if not state and not artifacts:
        raise HTTPException(status_code=404, detail=f"No state or artifacts for job {job_id}")

    summary = {k: v for k, v in state.items() if k not in BULK_FIELDS}
    summary["job_id"] = job_id
    summary["artifacts"] = artifacts

    # total_cost, best source first:
    #   1. {job_id}/total_cost.json  finalized from the authoritative session_events
    #                                array; outlives the doc
    #   2. doc total_cost            node 3's running sum — the only source mid-run
    #                                or after a hard crash
    #   3. sum of the incremental log  only if the run died before node 3 reported
    # `is None` throughout, not truthiness: a genuine 0.0 cost must not fall through.
    incremental = state.get("session_events_incremental") or []
    cost, cost_source = None, None
    if "total_cost.json" in artifacts:
        try:
            cost_doc = json.loads(bucket.blob(f"{job_id}/total_cost.json").download_as_text())
            cost, cost_source = cost_doc.get("total_cost"), cost_doc.get("cost_source")
        except Exception as e:
            print(f"total_cost.json unreadable for {job_id}: {e}")
    if cost is None:
        cost, cost_source = state.get("total_cost"), "firestore_running_total"
    if cost is None:
        cost = round(sum(ev.get("cost") or 0 for ev in incremental), 6)
        cost_source = "session_events_incremental"
    summary["total_cost"] = cost
    summary["cost_source"] = cost_source

    # Counted from the doc's working log, NOT the bucket transcript: /summary is the
    # lightweight lookup, and downloading a multi-MB transcript just to length it
    # would defeat that. Omitted once the doc expires — use /transcript for a count
    # of an archived run.
    if snap.exists:
        summary["session_events_count"] = len(incremental)

    return summary


@app.get("/transcript/{job_id}")
def get_transcript(job_id: str):
    """The full event transcript, best source first.

    1. {job_id}/session_events.json             authoritative — the array the workflow
                                                itself shipped at terminal status
    2. {job_id}/session_events_incremental.json  archived working copy (/archive-incremental)
    3. Firestore session_events_incremental      live; the only source mid-run or
                                                 after a hard crash

    Serving it live means a consumer never needs the transcript copied anywhere to
    read it — only to *link* to it.
    """
    for name in ("session_events.json", "session_events_incremental.json"):
        blob = bucket.blob(f"{job_id}/{name}")
        if blob.exists():
            return json.loads(blob.download_as_text())

    snap = db.collection("job_states").document(job_id).get()
    if snap.exists:
        transcript = (snap.to_dict() or {}).get("session_events_incremental")
        if transcript:
            return sorted(transcript, key=lambda e: e.get("timestamp") or "")

    raise HTTPException(status_code=404, detail=f"No transcript for job {job_id}")


@app.post("/archive-incremental/{job_id}")
def archive_incremental(job_id: str):
    """Copy the live Firestore transcript into the bucket, for consumers that need a
    LINK rather than a response body (a spreadsheet cell, say).

    Deliberately a separate filename from session_events.json: that one is the
    authoritative array the workflow shipped at completion, this is the working copy
    reassembled from per-turn broadcasts. A crashed run only ever gets this one, and
    the distinct name keeps the provenance obvious at a glance.

    Not part of the normal run path — call it only when a consumer finds no
    session_events.json in the bucket. No-ops if the authoritative file exists.
    """
    final_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{job_id}/session_events.json"
    if bucket.blob(f"{job_id}/session_events.json").exists():
        return {"job_id": job_id, "archived": False,
                "reason": "authoritative session_events.json already exists",
                "url": final_url}

    snap = db.collection("job_states").document(job_id).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail=f"No state for job {job_id}")
    transcript = (snap.to_dict() or {}).get("session_events_incremental")
    if not transcript:
        raise HTTPException(status_code=404, detail=f"No transcript recorded for job {job_id}")

    transcript = sorted(transcript, key=lambda e: e.get("timestamp") or "")
    path = f"{job_id}/session_events_incremental.json"
    bucket.blob(path).upload_from_string(
        json.dumps(transcript, indent=2, default=str), content_type="application/json"
    )
    return {"job_id": job_id, "archived": True, "events": len(transcript),
            "url": f"https://storage.googleapis.com/{BUCKET_NAME}/{path}"}


HUMAN_ACTIONS = ("continue", "corrections", "message", "abort")

# A human who is demonstrably still working never gets cut off mid-sentence, but a
# runaway beacon with nobody behind it must not hold an execution open forever.
MAX_GATE_MINUTES = 30        # hard ceiling measured from when the gate opened
ACTIVITY_WRITE_DEBOUNCE = 2  # seconds; the UI throttles too, this just bounds the writes


def _parse_iso(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


@app.post("/human-activity/{job_id}")
def human_activity(job_id: str, probe: int = 0):
    """Beacon from the review UI: the human is typing or clicking, so push the gate's
    deadline out rather than letting it lapse.

    This is the whole of the "is anyone there?" mechanism. The pipeline pauses on an
    n8n Wait node whose time limit is fixed when it starts, so a running wait cannot be
    extended in place. Instead the wait runs in slices: when one lapses, the workflow
    reads the deadline this endpoint maintains, and if it now sits in the future it
    opens another slice instead of giving up. The effect is "the gate closes N minutes
    after the last sign of life", with the 30-second opening window in `timeout` mode
    becoming 30 seconds *of silence* rather than 30 seconds flat.

    Deliberately cheap and forgiving: no body, no auth beyond the job id, and a no-op
    (never an error) when no gate is open.

    `?probe=1` is the channel check the UI fires once when a gate opens. It records
    `beacon_ok` and deliberately does NOT extend anything, so the idle timer still means
    what it says. Its only job is to let the UI tell "nobody is typing" apart from
    "beacons are not arriving at all" — which is otherwise invisible and, on a stale
    deployment where this route 404s, looks exactly like a feature that does not work.

    Returns an Access-Control-Allow-Origin header because the browser calls it directly
    from another origin and must be able to READ the reply. A beacon that fails silently
    is worse than no beacon.
    """
    cors = {"Access-Control-Allow-Origin": "*"}
    doc_ref = db.collection("job_states").document(job_id)
    snap = doc_ref.get()
    if not snap.exists:
        return JSONResponse({"extended": False, "reason": "unknown job"}, headers=cors)
    state = snap.to_dict() or {}
    gate = state.get("human_review") or {}
    if state.get("status") != "awaiting_human" or gate.get("stage") not in ("gate", "conversation"):
        return JSONResponse({"extended": False, "reason": "no open gate"}, headers=cors)

    if probe:
        doc_ref.update({"human_review.beacon_ok": True})
        return JSONResponse({"extended": False, "reason": "probe", "beacon_ok": True}, headers=cors)

    now = datetime.now(timezone.utc)
    last = _parse_iso(gate.get("last_activity"))
    if last and (now - last).total_seconds() < ACTIVITY_WRITE_DEBOUNCE:
        return JSONResponse({"extended": False, "reason": "debounced"}, headers=cors)

    grace = int(gate.get("active_grace_seconds") or 600)
    deadline = now + timedelta(seconds=grace)
    opened = _parse_iso(gate.get("opened_at"))
    if opened:
        deadline = min(deadline, opened + timedelta(minutes=MAX_GATE_MINUTES))
    current = _parse_iso(gate.get("deadline"))
    if current and current > deadline:
        deadline = current   # never shorten a wait that was already longer

    # Dotted paths, not a read-modify-write of the whole block: a gate that closes
    # underneath us then gets overwritten wholesale by the next announce, instead of
    # this beacon resurrecting a stale resume_url.
    doc_ref.update({
        "human_review.last_activity": now.isoformat(),
        "human_review.deadline": deadline.isoformat(),
        "human_review.beacon_ok": True,
    })
    return JSONResponse({"extended": True, "deadline": deadline.isoformat()}, headers=cors)


@app.post("/human-decision/{job_id}")
async def human_decision(job_id: str, request: Request):
    """Relay a human's scaffolding-review decision to the paused n8n execution.

    The pipeline pauses on an n8n Wait node and publishes the node's one-shot resume
    URL in the job doc (`human_review.resume_url`, status "awaiting_human"). The
    frontend never talks to n8n directly: it posts here, we validate that a gate is
    actually open, stamp the decision on the doc so the UI reacts immediately, and
    forward it to the resume URL. n8n then takes over and broadcasts as usual.

    Body: { "action": "continue" | "corrections" | "message" | "abort",
            "message": "<free text — required for 'message', optional otherwise>" }
      continue     — the scaffold is fine (or, on the max-retries rescue, accept it as is)
      corrections  — open the conversation (message optional; the first real message
                     can follow as 'message')
      message      — a conversation turn for the QA Inspection Manager
      abort        — give up: the run ends as a clean failure
    """
    body = await request.json()
    action = (body or {}).get("action")
    message = str((body or {}).get("message") or "").strip()
    if action not in HUMAN_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {HUMAN_ACTIONS}")
    if action == "message" and not message:
        raise HTTPException(status_code=400, detail="'message' requires a non-empty message")
    # Corrections typed straight into the first box arrive as a real message.
    if action == "corrections" and message:
        action = "message"

    doc_ref = db.collection("job_states").document(job_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail=f"No state for job {job_id}")
    state = snap.to_dict() or {}
    gate = state.get("human_review") or {}
    resume_url = gate.get("resume_url")
    if state.get("status") != "awaiting_human" or not resume_url or gate.get("stage") in (None, "closed", "resuming"):
        raise HTTPException(status_code=409, detail="No open human-review gate for this job (it may have timed out or already been answered)")

    now = datetime.now(timezone.utc).isoformat()
    decision = {"job_id": job_id, "action": action, "message": message, "decided_at": now,
                "stage": gate.get("stage"), "round": gate.get("round", 0)}

    # Optimistic update FIRST: the UI drops the gate the moment we accept the click,
    # even if n8n takes a few seconds to resume and broadcast.
    doc_ref.set({"status": "running", "timestamp": now,
                 "human_decisions": firestore.ArrayUnion([decision])}, merge=True)
    doc_ref.update({"human_review": {**gate, "stage": "resuming", "last_action": action,
                                     "last_message": message, "resumed_at": now}})

    try:
        resp = requests.post(resume_url, json=decision, timeout=20)
    except requests.RequestException as e:
        doc_ref.set({"status": "awaiting_human"}, merge=True)   # roll back: the gate is still open
        doc_ref.update({"human_review": gate})
        raise HTTPException(status_code=502, detail=f"Could not reach the pipeline to resume it: {e}")

    if resp.status_code == 404:
        # The Wait already resumed (timeout) — nothing to do; n8n will broadcast shortly.
        raise HTTPException(status_code=409, detail="The pipeline already moved on (the wait timed out before this decision arrived)")
    if resp.status_code >= 400:
        doc_ref.set({"status": "awaiting_human"}, merge=True)
        doc_ref.update({"human_review": gate})
        raise HTTPException(status_code=502, detail=f"Pipeline refused the resume call: HTTP {resp.status_code} {resp.text[:300]}")

    return {"status": "resumed", "job_id": job_id, "action": action, "n8n_status": resp.status_code}


@app.get("/")
def read_root():
    return {"status": "Listener is active"}
