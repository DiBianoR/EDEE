from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from google.cloud import firestore, storage
from google.api_core.exceptions import NotFound, PreconditionFailed
import os
import json
import base64
import hashlib
import requests
# timedelta was missing from this import from 2026-09-22 until 2026-11-21, so every real
# /human-activity beacon died with a NameError (500) and typing never extended a gate;
# only the ?probe=1 channel check, which returns before the arithmetic, worked.
from datetime import datetime, timezone, timedelta
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

# Firestore holds a job only while it is live. When a run ends, everything in the doc is
# written to the bucket ({job_id}/job.json + session_events.json) and the doc's TTL is cut
# to this, after which Firestore deletes it. Readers (/summary, /restore, the frontend)
# fall back to the bucket, so a job stays viewable and resumable after its doc is gone.
FINISHED_DOC_TTL = timedelta(days=1)
LIVE_DOC_TTL = timedelta(days=7)    # what the frontend stamps at start; restored docs get it too

# A run that crashes sends nothing more, so nothing above ever archives it, and if no one
# had the page open (the frontend's watchdog calls /archive) its transcript would exist
# only in a doc due to expire. So the broadcasts of LATER jobs sweep for such runs: there
# is no timer and nothing runs while the system is idle. Bounded on every axis: at most
# once per SWEEP_EVERY per instance, one query that returns only unfinished docs (crashes
# are rare, and the TTL keeps at most a week of them), at most SWEEP_MAX archived per sweep.
STALE_AFTER = timedelta(hours=6)     # no broadcast for this long = dead (a human gate caps at 30 min)
SWEEP_EVERY = timedelta(hours=1)
SWEEP_MAX = 5
_last_sweep = None


# =====================================================================================
# IMAGE VERSIONS
# =====================================================================================
# Every distinct image a job produces is kept, exactly ONCE, and named by this service
# only (n8n just sends bytes and never learns a name):
#
#   {job_id}/images/NNN-<source>.png   every version, one counter shared by all sources,
#                                      so name order IS chronological order
#   {job_id}/scaffolding.png           the accepted scaffold   } a version MOVES here when it
#   {job_id}/final_illustration.png    the released picture    } takes the role, and back to
#                                                                its numbered name when it
#                                                                loses it: never a 2nd copy
#   {job_id}/images.json               the manifest: every version, where it lives now,
#                                      and which version holds each role
#
# The two role names stay because the n8n resume nodes and the Test Orchestrator read them
# directly. Duplicates are caught by MD5 (GCS keeps one per object): the resume flow and the
# final broadcast re-send images this job already has, and those become no-ops.
#
# Sources: scaffold (the matplotlib render), artist (the painted illustration), edit (a spot
# edit), resumed (a "new job" resume's copy of the source job's final; one copy per new job
# is inherent to that mode). Versions are addressed by `n`, which is what a future "resume
# from this version" would take.
#
# latest.png is no longer written; `live_image` on the doc names the current version.
# Jobs from before this change keep theirs (adopted lazily, see _adopt_legacy) until they
# complete, when it is deleted as before.
IMAGE_SOURCES = {"scaffolding_generator": "scaffold", "artist": "artist",
                 "image_editor": "edit", "final_gate": "resumed"}
ROLE_FILES = {"scaffolding": "scaffolding.png", "final": "final_illustration.png"}
MANIFEST_NAME = "images.json"
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_placeholder_md5 = None


def _md5(data):
    return base64.b64encode(hashlib.md5(data).digest()).decode("ascii")


def _is_placeholder(blob):
    """The stock "no scaffolding" filler, copied in on DIRECT_IMAGE_GEN runs. Not a version."""
    global _placeholder_md5
    if _placeholder_md5 is None:
        stock = storage_client.bucket(PLACEHOLDER_BUCKET).get_blob(PLACEHOLDER_BLOB)
        _placeholder_md5 = stock.md5_hash if stock else ""
    return bool(blob.md5_hash) and blob.md5_hash == _placeholder_md5


class ImageVersions:
    """One job's image manifest. Load, change, save(); save() refuses if another writer
    saved in between (GCS generation precondition), and the caller retries from scratch."""

    def __init__(self, job_id):
        self.job_id = job_id
        self.dirty = False               # set before _adopt_legacy, which makes it True
        self.uploaded = []               # new numbered files, removed again if save() loses a race
        blob = bucket.get_blob(self._path(MANIFEST_NAME))
        if blob:
            self.generation = blob.generation
            self.data = json.loads(blob.download_as_text(if_generation_match=blob.generation))
        else:
            self.generation = 0          # "must not exist yet"
            self.data = {"version": 1, "next": 1, "images": [], "scaffolding": None, "final": None}
            self._adopt_legacy()

    def _path(self, name):
        return f"{self.job_id}/{name}"

    def _numbered(self, e):
        return f"images/{e['n']:03d}-{e['source']}.{EXT.get(e.get('mime'), 'png')}"

    def entry(self, n):
        return next((e for e in self.data["images"] if e["n"] == n), None)

    def _new_entry(self, md5, mime, source, agent=None, task=None, created=None, size=None, legacy=False):
        e = {"n": self.data["next"], "source": source, "agent": agent, "task": task, "mime": mime,
             "md5": md5, "size": size, "created": created or datetime.now(timezone.utc).isoformat()}
        if legacy:
            e["legacy"] = True   # written before versioning existed: earlier versions are gone
        self.data["next"] += 1
        self.data["images"].append(e)
        self.dirty = True
        return e

    def _adopt_legacy(self):
        """A job with no manifest yet: take over whatever role images it already has,
        in place, so they are never overwritten or copied."""
        for role, name in ROLE_FILES.items():
            blob = bucket.get_blob(self._path(name))
            if not blob:
                continue
            if role == "scaffolding" and _is_placeholder(blob):
                self.data["scaffolding_placeholder"] = True
                continue
            e = self._new_entry(blob.md5_hash, (blob.content_type or "image/png").split(";")[0],
                                "scaffold" if role == "scaffolding" else "artist",
                                created=blob.time_created.isoformat() if blob.time_created else None,
                                size=blob.size, legacy=True)
            e["file"] = name
            self.data[role] = e["n"]

    def _move(self, src_name, dst_name, cache_control):
        src = bucket.blob(self._path(src_name))
        dst = bucket.copy_blob(src, bucket, self._path(dst_name))   # server-side
        dst.cache_control = cache_control
        dst.patch()
        src.delete()

    def add(self, data, mime, source, agent=None, task=None):
        """The version holding these bytes, stored now if this job doesn't have it yet."""
        md5 = _md5(data)
        found = next((e for e in self.data["images"] if e["md5"] == md5), None)
        if found:
            return found
        e = self._new_entry(md5, mime, source, agent, task, size=len(data))
        e["file"] = self._numbered(e)
        blob = bucket.blob(self._path(e["file"]))
        blob.cache_control = "public, max-age=86400"   # a numbered name never changes content
        blob.upload_from_string(data, content_type=mime)
        self.uploaded.append(blob)
        return e

    def pin(self, role, e):
        """Give `e` the role: it moves to the role's file name, and the version that held
        the role moves back to its numbered name. Each image still exists exactly once."""
        target = ROLE_FILES[role]
        if self.data.get(role) == e["n"] and e["file"] == target:
            return
        current = self.entry(self.data.get(role)) if self.data.get(role) else None
        if current and current is not e:
            back = self._numbered(current)
            self._move(target, back, "public, max-age=86400")
            current["file"] = back
        elif role == "scaffolding" and self.data.pop("scaffolding_placeholder", None):
            bucket.blob(self._path(target)).delete()      # the stock filler, not a version
        if e["file"] != target:
            if e["file"] in ROLE_FILES.values():
                # Already holding the OTHER role with identical bytes: the one case where a
                # second copy is unavoidable, since both names must resolve.
                src = bucket.blob(self._path(e["file"]))
                dst = bucket.copy_blob(src, bucket, self._path(target))
                dst.cache_control = "no-cache, max-age=0"
                dst.patch()
            else:
                self._move(e["file"], target, "no-cache, max-age=0")   # role names are mutable
                e["file"] = target
        self.data[role] = e["n"]
        self.dirty = True

    def placeholder_scaffold(self):
        """No scaffold this run: the stock filler takes the scaffolding name."""
        if self.data.get("scaffolding_placeholder"):
            return
        current = self.entry(self.data.get("scaffolding")) if self.data.get("scaffolding") else None
        if current:
            back = self._numbered(current)
            self._move(ROLE_FILES["scaffolding"], back, "public, max-age=86400")
            current["file"] = back
        src_bucket = storage_client.bucket(PLACEHOLDER_BUCKET)
        copied = src_bucket.copy_blob(src_bucket.blob(PLACEHOLDER_BLOB), bucket, self._path(ROLE_FILES["scaffolding"]))
        copied.content_type = "image/jpeg"   # cosmetic; .png name, .jpg bytes
        copied.patch()
        self.data["scaffolding"] = None
        self.data["scaffolding_placeholder"] = True
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        blob = bucket.blob(self._path(MANIFEST_NAME))
        blob.cache_control = "no-cache, max-age=0"
        blob.upload_from_string(json.dumps(self.data, indent=2), content_type="application/json",
                                if_generation_match=self.generation)
        self.generation = blob.generation
        self.dirty = False


def _with_manifest(job_id, change):
    """Run change(ImageVersions) and save, retrying if another writer got in first. One
    job's broadcasts arrive one at a time in practice; this only guards the odd overlap."""
    for attempt in range(3):
        iv = ImageVersions(job_id)
        result = change(iv)
        try:
            iv.save()
            return result
        except PreconditionFailed:
            for blob in iv.uploaded:     # never leave an orphan copy behind
                try:
                    blob.delete()
                except NotFound:
                    pass
            print(f"images.json changed under us for job {job_id} (attempt {attempt + 1}); retrying")
    raise HTTPException(status_code=503, detail=f"images.json for job {job_id} kept changing; try again")


def _record_images(job_id, status, agent_id, task_id, img, img_mime, scaff, scaff_mime, scaff_placeholder):
    """Store this broadcast's images. Returns the new `live_image` stamp, or None."""
    def change(iv):
        # Scaffold first, so a "new job" resume numbers its scaffold before its picture.
        if scaff:
            iv.pin("scaffolding", iv.add(scaff, scaff_mime, "scaffold", "scaffolding_generator", "archive_scaffolding"))
        elif scaff_placeholder:
            iv.placeholder_scaffold()
        if not img:
            return None
        e = iv.add(img, img_mime, IMAGE_SOURCES.get(agent_id, "image"), agent_id, task_id)
        if status == "completed":
            iv.pin("final", e)
        return e

    e = _with_manifest(job_id, change)
    if status == "completed":
        legacy = bucket.blob(f"{job_id}/latest.png")   # jobs that started before versioning
        if legacy.exists():
            legacy.delete()
    if not e:
        return None
    blob = bucket.get_blob(f"{job_id}/{e['file']}")
    return {"name": e["file"], "version": str(blob.generation) if blob else None, "n": e["n"],
            "updated": datetime.now(timezone.utc).isoformat()}


# =====================================================================================
# JOB RECORD IN THE BUCKET
# =====================================================================================
TRANSIENT_FIELDS = ("ttl_expiry",)


def _write_job_record(job_id, state):
    """{job_id}/job.json: the doc minus the transcript (that is session_events.json)."""
    record = {k: v for k, v in state.items() if k not in BULK_FIELDS and k not in TRANSIENT_FIELDS}
    record["job_id"] = job_id
    record["archived_at"] = datetime.now(timezone.utc).isoformat()
    blob = bucket.blob(f"{job_id}/job.json")
    blob.cache_control = "no-cache, max-age=0"
    blob.upload_from_string(json.dumps(record, indent=2, default=str), content_type="application/json")
    return record


def _read_json(path):
    blob = bucket.get_blob(path)
    return json.loads(blob.download_as_text()) if blob else None


def _bucket_transcript(job_id):
    for name in ("session_events.json", "session_events_incremental.json"):
        data = _read_json(f"{job_id}/{name}")
        if data is not None:
            return data
    return None


def _legacy_record(job_id, artifacts):
    """LEGACY (jobs finished before job.json existed, whose Firestore doc has expired).
    Rebuilds the doc fields readers use from the transcript. Remove once such jobs no
    longer matter; everything finished after 2026-11-21 has a job.json."""
    transcript = _bucket_transcript(job_id)
    if transcript is None:
        return None
    rec = {"job_id": job_id, "record_source": "legacy_transcript", "status_inferred": True,
           "status": "completed" if "final_illustration.png" in artifacts else "failed"}
    for ev in sorted(transcript, key=lambda e: e.get("timestamp") or ""):
        if "original_query" not in rec:
            # The first prompt quotes it: User Input: ```<the query>```
            for p in ev.get("parts") or []:
                text = p.get("text") if isinstance(p, dict) else None
                if isinstance(text, str) and text.startswith("User Input:") and "```" in text:
                    rec["original_query"] = text.split("```")[1].strip()
                    break
        for p in ev.get("parts") or []:
            try:
                obj = json.loads(p.get("text") or "")
            except (ValueError, TypeError, AttributeError):
                continue
            if not isinstance(obj, dict):
                continue
            for k in ("user_message", "archival_report", "generation_successful", "latest_description"):
                if k in obj:
                    rec[k] = obj[k]
            if ev.get("author") == "error_handler" and obj.get("error_message"):
                rec["error_message"] = obj["error_message"]
        rec["timestamp"] = ev.get("timestamp") or rec.get("timestamp")
    return rec


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

        # Every image becomes a version (see IMAGE VERSIONS above). A mid-run render is
        # stored under its numbered name; status "completed" also gives it the final role;
        # the scaffold (sent by the n8n "Archive Scaffolding" node at the END OF PHASE 3,
        # status-agnostic on purpose) takes the scaffolding role.
        #
        # `live_image` announces WHICH version is current, on the doc the frontend already
        # polls every tick: a durable field that changes exactly when the image changes,
        # so a render landing between two polls cannot be missed. (Its name used to be
        # latest.png every time, overwritten in place; now each version has its own.)
        if base64_img or scaff_base64 or scaff_placeholder:
            live = _record_images(
                job_id, status, data.get("agent_id"), data.get("task_id"),
                base64.b64decode(base64_img) if base64_img else None, mime_type,
                base64.b64decode(scaff_base64) if scaff_base64 else None, scaff_mime, scaff_placeholder)
            if live:
                top_level_data["live_image"] = live

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

            # Everything else the doc knows goes to the bucket too, and the doc is left to
            # expire: from here on it is only a fast cache for anyone still watching.
            _write_job_record(job_id, state)
            doc_ref.update({"ttl_expiry": datetime.now(timezone.utc) + FINISHED_DOC_TTL})

        _sweep_stale_runs()
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
    if not state:
        # The doc has expired (finished jobs keep it for a day): the bucket copy answers.
        if "job.json" in artifacts:
            state = _read_json(f"{job_id}/job.json") or {}
            state["record_source"] = "job.json"
        else:
            state = _legacy_record(job_id, artifacts) or {}

    summary = {k: v for k, v in state.items() if k not in BULK_FIELDS}
    summary["job_id"] = job_id
    summary["artifacts"] = artifacts
    summary["images"] = _read_json(f"{job_id}/{MANIFEST_NAME}") if MANIFEST_NAME in artifacts else None
    # latest.png is no longer written, but the Test Orchestrator still reads it from this
    # manifest for an unfinished run ("the last render, scaffold or artist"). Point the
    # old name at the newest version, so it keeps working without a copy.
    images = (summary["images"] or {}).get("images") or []
    if images and "latest.png" not in artifacts and "final_illustration.png" not in artifacts:
        newest = artifacts.get(max(images, key=lambda e: e["n"])["file"])
        if newest:
            artifacts["latest.png"] = dict(newest, alias=True)

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


# Text only. Images are never copied: a version keeps living in the job (it just moves
# back to its numbered name when a new one takes its role), so the backup records WHICH
# versions this round had instead.
ROUND_FILES = ("session_events.json", "total_cost.json", "job.json")


@app.post("/backup-round/{job_id}")
def backup_round(job_id: str):
    """Back up a finished job's result into {job_id}/previous/<UTC timestamp>/ before a
    "Resume final review" round overwrites it (n8n "Resume: Rebuild State", overwrite
    mode). Server-side copies: the bytes never leave the bucket.

    One folder per round, so a second resume can't erase the first one's backup. The
    workflow refuses to overwrite a job when this call fails, so it 404s rather than
    "succeeding" with nothing to back up.

    Images: round.json names the versions that held the scaffolding and final roles. A job
    from before versioning has its role images adopted into images.json here, in place,
    so the coming round moves them aside instead of overwriting them. (Backups made before
    2026-11-21 hold full image copies instead.)
    """
    if not bucket.blob(f"{job_id}/final_illustration.png").exists():
        raise HTTPException(status_code=404, detail=f"Job {job_id} has no final_illustration.png to back up")
    roles = _with_manifest(job_id, lambda iv: {"scaffolding": iv.data.get("scaffolding"),
                                               "scaffolding_placeholder": bool(iv.data.get("scaffolding_placeholder")),
                                               "final": iv.data.get("final")})
    folder = f"{job_id}/previous/{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')}"
    copied = []
    for name in ROUND_FILES:
        src = bucket.blob(f"{job_id}/{name}")
        if src.exists():
            bucket.copy_blob(src, bucket, f"{folder}/{name}")
            copied.append(name)
    bucket.blob(f"{folder}/round.json").upload_from_string(
        json.dumps({"image_versions": roles, "manifest": MANIFEST_NAME}, indent=2), content_type="application/json")
    copied.append("round.json")
    return {"job_id": job_id, "folder": folder, "copied": copied, "image_versions": roles}


@app.post("/archive/{job_id}")
def archive_job(job_id: str):
    """Write a job that never reached a terminal status (a crash, or a run the frontend's
    watchdog gave up on) to the bucket: job.json plus, if no finished transcript exists,
    the working one. Then let its doc expire like a finished job's.

    Idempotent. If the run later revives and finishes, its terminal broadcast overwrites
    job.json and resets the TTL as usual.
    """
    doc_ref = db.collection("job_states").document(job_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail=f"No live state for job {job_id}")
    return {"job_id": job_id, "archived": _archive(doc_ref, snap.to_dict() or {})}


def _archive(doc_ref, state):
    job_id = doc_ref.id
    _write_job_record(job_id, state)
    archived = ["job.json"]
    transcript = state.get("session_events_incremental")
    if transcript and not bucket.blob(f"{job_id}/session_events.json").exists():
        bucket.blob(f"{job_id}/session_events_incremental.json").upload_from_string(
            json.dumps(sorted(transcript, key=lambda e: e.get("timestamp") or ""), indent=2, default=str),
            content_type="application/json")
        archived.append("session_events_incremental.json")
    # archived_at lets the sweep skip this doc until the run broadcasts again.
    now = datetime.now(timezone.utc)
    doc_ref.update({"ttl_expiry": now + FINISHED_DOC_TTL, "archived_at": now.isoformat()})
    return archived


def _aware(value):
    """A doc time as an aware datetime: Firestore gives datetimes, broadcasts ISO strings."""
    d = value if isinstance(value, datetime) else _parse_iso(value) if value else None
    return d.replace(tzinfo=timezone.utc) if d and d.tzinfo is None else d


def _sweep_stale_runs():
    """Archive unfinished runs that have gone quiet (see STALE_AFTER). Called from
    /update-state; never raises, so a sweep problem can't fail a broadcast."""
    global _last_sweep
    now = datetime.now(timezone.utc)
    if _last_sweep and now - _last_sweep < SWEEP_EVERY:
        return
    _last_sweep = now
    try:
        # Only the small fields of only the unfinished docs (not-in skips docs with no status).
        snaps = (db.collection("job_states").where("status", "not-in", ["completed", "failed"])
                 .select(["status", "timestamp", "archived_at", "ttl_expiry"]).limit(200).stream())
        stale = []
        for s in snaps:
            d = s.to_dict() or {}
            last = _aware(d.get("timestamp"))
            if last is None and _aware(d.get("ttl_expiry")):
                last = _aware(d["ttl_expiry"]) - LIVE_DOC_TTL   # a start stub that never broadcast
            if last is None or now - last < STALE_AFTER:
                continue
            archived_at = _aware(d.get("archived_at"))
            if archived_at and archived_at >= last:
                continue
            stale.append(s.id)
        for job_id in stale[:SWEEP_MAX]:
            ref = db.collection("job_states").document(job_id)
            snap = ref.get()
            if snap.exists:
                print(f"SWEEP archived stale run {job_id}: {_archive(ref, snap.to_dict() or {})}")
    except Exception as e:  # noqa: BLE001
        print(f"SWEEP failed: {type(e).__name__}: {e}")


@app.post("/restore/{job_id}")
def restore_job(job_id: str):
    """Put an expired job back into Firestore from the bucket, before something resumes
    it in place (the frontend calls this before an overwrite-mode resume, so the live log
    shows the job's history and not just the new round). Viewing an old job never needs
    this: readers fall back to the bucket on their own.

    No-op when the doc is still there with its transcript.
    """
    doc_ref = db.collection("job_states").document(job_id)
    snap = doc_ref.get()
    if snap.exists and (snap.to_dict() or {}).get("session_events_incremental"):
        return {"job_id": job_id, "restored": False, "reason": "doc is live"}
    record = _read_json(f"{job_id}/job.json")
    if record is None:
        artifacts = {b.name.split("/", 1)[-1]: True for b in storage_client.list_blobs(bucket, prefix=f"{job_id}/")}
        record = _legacy_record(job_id, artifacts)
    transcript = _bucket_transcript(job_id)
    if record is None and transcript is None:
        raise HTTPException(status_code=404, detail=f"Nothing in the bucket for job {job_id}")
    record = {k: v for k, v in (record or {}).items() if k not in ("archived_at", "record_source", "status_inferred")}
    record["session_events_incremental"] = sorted(transcript or [], key=lambda e: e.get("timestamp") or "")
    record["ttl_expiry"] = datetime.now(timezone.utc) + LIVE_DOC_TTL
    doc_ref.set(record, merge=True)
    return {"job_id": job_id, "restored": True, "events": len(record["session_events_incremental"])}


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
