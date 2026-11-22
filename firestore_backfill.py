"""
One-off: copy every job still in Firestore to the bucket, so the Firestore TTL policy
can be switched on without losing anything.

    py -3 firestore_backfill.py          # dry run: lists what it would archive
    py -3 firestore_backfill.py --go     # archives them

Why: since the image-versions change, the state manager writes {job}/job.json to the
bucket when a job ends and lets its Firestore doc expire a day later. Docs from before
that change have no job.json, and a crashed run may have its transcript ONLY in the
doc. Once the TTL policy on job_states.ttl_expiry is on, Firestore deletes every doc
whose expiry has passed (nearly all old ones) within a day or so. Run this first.

For each doc it calls the state manager's POST /archive/{job_id}, which writes job.json
(plus session_events_incremental.json when the bucket has no finished transcript) and
sets the doc to expire in a day. That endpoint is idempotent, so rerunning this is safe,
and a run that later revives and finishes overwrites job.json as usual.

Skipped: jobs that look live (not completed/failed, updated within --live-hours),
because /archive would set their doc to expire in a day while they may still be in a
human review gate. Rerun later to pick them up, or they'll archive themselves at the end.

Order of operations:
  1. Push, so the state manager with /archive is deployed (this script checks).
  2. py -3 firestore_backfill.py --go
  3. Turn on the TTL policy (Firestore > Time-to-live, collection job_states, field
     ttl_expiry, database dee-data).

Needs: pip install google-cloud-firestore google-cloud-storage requests
Credentials, first found: FIRESTORE_KEY_JSON (same as the frontend), --key <file>, the
gen-lang-client-*.json key in this folder, then Application Default Credentials.
"""

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

PROJECT_ID = "gen-lang-client-0925957935"
DB_NAME = "dee-data"
BUCKET_NAME = "edee-job-archives-0925957935"
STATE_MANAGER_URL = os.environ.get("STATE_MANAGER_URL", "https://ritel-state-manager-194521282716.us-south1.run.app")
TERMINAL = ("completed", "failed")
HERE = os.path.dirname(os.path.abspath(__file__))


def clients(key_path=None):
    from google.cloud import firestore, storage
    from google.oauth2 import service_account
    info = None
    if "FIRESTORE_KEY_JSON" in os.environ:
        info = json.loads(os.environ["FIRESTORE_KEY_JSON"])
    else:
        path = key_path or next(iter(sorted(glob.glob(os.path.join(HERE, "gen-lang-client-*.json")))), None)
        if path:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
    if info:
        creds = service_account.Credentials.from_service_account_info(info)
        return (firestore.Client(credentials=creds, project=PROJECT_ID, database=DB_NAME),
                storage.Client(credentials=creds, project=PROJECT_ID).bucket(BUCKET_NAME))
    return (firestore.Client(project=PROJECT_ID, database=DB_NAME),
            storage.Client(project=PROJECT_ID).bucket(BUCKET_NAME))


def as_datetime(value):
    """Doc timestamps are ISO strings (broadcasts) or Firestore datetimes (ttl_expiry)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def check_endpoint():
    """The deployed state manager must have /archive. A missing route and a missing job
    both answer 404, so tell them apart by the message."""
    try:
        r = requests.post(f"{STATE_MANAGER_URL}/archive/__backfill_probe__", timeout=30)
    except requests.RequestException as e:
        sys.exit(f"Can't reach the state manager at {STATE_MANAGER_URL}: {e}")
    detail = ""
    try:
        detail = str(r.json().get("detail", ""))
    except ValueError:
        pass
    if r.status_code == 404 and "No live state" in detail:
        return
    sys.exit(f"The deployed state manager has no /archive endpoint yet (HTTP {r.status_code}: "
             f"{detail or r.text[:120]}). Push first, then rerun.")


def classify(docs, live_hours, now):
    """Split (job_id, fields) pairs into (to_archive, skipped) lists of (job_id, reason)."""
    to_archive, skipped = [], []
    cutoff = now - timedelta(hours=live_hours)
    for job_id, d in docs:
        status = d.get("status") or "?"
        last = as_datetime(d.get("timestamp"))
        if status not in TERMINAL and last and last > cutoff:
            skipped.append((job_id, f"looks live: {status}, updated {last:%Y-%m-%d %H:%M} UTC"))
            continue
        when = f"{last:%Y-%m-%d}" if last else "no timestamp"
        to_archive.append((job_id, f"{status}, {when}"))
    return to_archive, skipped


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--go", action="store_true", help="archive (without it: dry run)")
    p.add_argument("--live-hours", type=float, default=24,
                   help="skip unfinished jobs updated within this many hours (default 24)")
    p.add_argument("--key", help="service-account key file")
    p.add_argument("--pause", type=float, default=0.2, help="seconds between calls (default 0.2)")
    a = p.parse_args(argv)

    if a.go:
        check_endpoint()
    db, bucket = clients(a.key)
    # Only the small fields: a doc's transcript can be most of a megabyte.
    snaps = db.collection("job_states").select(["status", "timestamp"]).stream()
    docs = [(s.id, s.to_dict() or {}) for s in snaps]
    to_archive, skipped = classify(docs, a.live_hours, datetime.now(timezone.utc))

    print(f"{len(docs)} docs in job_states: {len(to_archive)} to archive, {len(skipped)} skipped.")
    for job_id, why in skipped:
        print(f"  skip  {job_id}  ({why})")
    if not a.go:
        for job_id, why in to_archive:
            print(f"  would archive  {job_id}  ({why})")
        print("Dry run. Add --go to archive.")
        return 0

    done, failed = 0, []
    for i, (job_id, why) in enumerate(to_archive, 1):
        try:
            r = requests.post(f"{STATE_MANAGER_URL}/archive/{job_id}", timeout=60)
            if r.status_code == 404:
                print(f"  [{i}/{len(to_archive)}] {job_id}  gone already (expired meanwhile)")
                continue
            r.raise_for_status()
            if not bucket.blob(f"{job_id}/job.json").exists():
                raise RuntimeError("archive returned OK but job.json isn't in the bucket")
            done += 1
            print(f"  [{i}/{len(to_archive)}] {job_id}  {', '.join(r.json().get('archived', []))}")
        except Exception as e:  # noqa: BLE001 - report and carry on; rerunning is safe
            failed.append((job_id, str(e)[:200]))
            print(f"  [{i}/{len(to_archive)}] {job_id}  FAILED: {str(e)[:200]}")
        time.sleep(a.pause)

    print(f"\nArchived {done}, failed {len(failed)}, skipped {len(skipped)} live-looking.")
    if failed:
        print("Rerun to retry the failures (archiving is idempotent).")
        return 1
    print("Safe to turn on the TTL policy once the skipped jobs (if any) have finished or been rerun.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
