from fastapi import FastAPI, Request, HTTPException
from google.cloud import firestore, storage
import os
import json
import base64
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

        return {"status": "success", "message": f"Updated job {job_id}"}
        
    except Exception as e:
        print(f"Error updating state: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def read_root():
    return {"status": "Listener is active"}
