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
        
        # 1. Package the agent's thought process
        log_entry = None
        if "response" in data or "query" in data:
            log_entry = {
                "timestamp": data.get("timestamp"),
                "phase_id": data.get("phase_id"),
                "agent_id": data.get("agent_id"),
                "task_id": data.get("task_id"),
                "query": data.get("query"),
                "response": data.get("response")
            }

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

        # Additional copy of history to bucket (On completion)
        history_data = top_level_data.get("history")
        if history_data:
            history_path = f"{job_id}/history.json"
            history_blob = bucket.blob(history_path)
            history_blob.upload_from_string(
                json.dumps(history_data, indent=2),
                content_type="application/json"
            )

        # 4. Append thought log to Firestore array
        if log_entry:
            top_level_data["logs"] = firestore.ArrayUnion([log_entry])
        
        doc_ref.set(top_level_data, merge=True)
        
        return {"status": "success", "message": f"Updated job {job_id}"}
        
    except Exception as e:
        print(f"Error updating state: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def read_root():
    return {"status": "Listener is active"}