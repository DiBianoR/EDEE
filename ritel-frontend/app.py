import streamlit as st
import streamlit.components.v1 as components
import requests
import base64
import time
import uuid
import os
import json
import io
import zipfile
from datetime import datetime, timedelta, timezone
from google.cloud import firestore, storage
from google.oauth2 import service_account

# --- CONFIGURATION ---
N8N_START_URL = os.environ.get("N8N_START_URL", "https://edee.app.n8n.cloud/webhook/generate-diagram")
API_KEY = os.environ.get("API_KEY", "mathisfun") 
MAX_TIMEOUT = 180  # 3 Minute Safety Timeout

# --- GCP INITIALIZATION ---
@st.cache_resource
def get_gcp_clients():
    if "FIRESTORE_KEY_JSON" in os.environ:
        key_dict = json.loads(os.environ.get("FIRESTORE_KEY_JSON"))
        credentials = service_account.Credentials.from_service_account_info(key_dict)
        db = firestore.Client(credentials=credentials, project=key_dict.get("project_id"), database="dee-data")
        st_client = storage.Client(credentials=credentials, project=key_dict.get("project_id"))
    else:
        db = firestore.Client(project="gen-lang-client-0925957935", database="dee-data")
        st_client = storage.Client(project="gen-lang-client-0925957935")
    return db, st_client

db, storage_client = get_gcp_clients()
bucket = storage_client.bucket("edee-job-archives-0925957935")

# --- PAGE SETUP ---
st.set_page_config(page_title="EDEE Gen", layout="wide", page_icon="📐")

# --- INITIALIZE SESSION STATE ---
if "job_id" not in st.session_state:
    st.session_state.job_id = None
if "is_running" not in st.session_state:
    st.session_state.is_running = False
# Added trigger state for the button callback
if "trigger_job" not in st.session_state:
    st.session_state.trigger_job = False
if "carousel_idx" not in st.session_state:
    st.session_state.carousel_idx = 1  # Default to 1 (the final image)

# --- CALLBACK FUNCTIONS ---
def start_job_callback():
    st.session_state.is_running = True
    st.session_state.trigger_job = True

def prev_image():
    st.session_state.carousel_idx -= 1

def next_image():
    st.session_state.carousel_idx += 1

# --- AGGRESSIVE CSS FOR TIGHT LAYOUT ---
st.markdown("""
    <style>
        .block-container { padding-top: 3.6rem !important; }
        .edee-title { margin: 0px !important; padding: 0px !important; font-size: 2.2rem; font-weight: 600; }
        h3 { margin-top: 0rem !important; padding-top: 0px !important; }
        .entry { border-bottom: 1px solid #444; padding-bottom: 10px; margin-bottom: 10px; font-family: monospace; }
        .instruction { color: #4DA8DA; margin-bottom: 8px; font-style: italic; font-size: 14px;}
        .key { color: #F6D55C; font-weight: bold; font-size: 13px;}
        .val { color: #81C784; font-size: 13px;}
    </style>
    <div class='edee-title'>📐 EDEE - Educational Diagram Generator</div>
""", unsafe_allow_html=True)

# --- SIDEBAR ---
with st.sidebar:
    st.header("Settings")
    env_choice = st.selectbox("Environment", ["Production", "Development (Test)"])
    style_choice = st.selectbox("Style", ["casual_mobile", "storybook", "cel_shaded_anime", "claymation_diorama", "mid_century_modern"])

# --- MAIN LAYOUT ---
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Input")
    user_query = st.text_area("Math Problem Description", height=150)
    # Added on_click to instantly disable the button
    generate_btn = st.button("Generate Diagram", type="primary", use_container_width=True, disabled=st.session_state.is_running, on_click=start_job_callback)
    
    st.markdown("<hr style='margin-top: 5px; margin-bottom: -15px; border: 0; border-top: 1px solid #444;'>", unsafe_allow_html=True) 
    
    st.subheader("Live Progress & Result")
    status_ui = st.empty()
    agent_ui = st.empty()
    image_ui = st.empty()
    notes_ui = st.empty()
    download_ui = st.empty()

with col2:
    st.subheader("Agent Thought Log")
    log_placeholder = st.empty()
    if not st.session_state.job_id:
        log_placeholder.markdown('<div style="height: 400px; background-color: #1E1E1E; border-radius: 8px; border: 1px solid #333;"></div>', unsafe_allow_html=True)

# --- IN-MEMORY ZIP GENERATOR ---
def generate_zip_bundle(state):
    zip_buffer = io.BytesIO()
    job_id = state.get("job_id")
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        # Pull Images from Cloud Storage
        if state.get("status") == "completed":
            try:
                img_bytes = bucket.blob(f"{job_id}/final_illustration.png").download_as_bytes()
                zip_file.writestr("final_illustration.png", img_bytes)
            except Exception: pass
            
            try:
                scaff_bytes = bucket.blob(f"{job_id}/scaffolding.png").download_as_bytes()
                zip_file.writestr("scaffolding.png", scaff_bytes)
            except Exception: pass
            
        # Pull Text from Firestore
        if state.get("archival_report"):
            zip_file.writestr("archival_report.txt", str(state["archival_report"]))
        if state.get("history"):
            zip_file.writestr("history.json", json.dumps(state["history"], indent=2))
            
        # Bundle remaining metadata
        metadata = {k: v for k, v in state.items() if k not in ["logs", "history", "archival_report", "ttl_expiry"]}
        zip_file.writestr("metadata.json", json.dumps(metadata, indent=2))
        
    return zip_buffer.getvalue()

# --- UI RENDERING HELPER FUNCTION ---
def draw_ui_state(state, last_log_index, log_content):
    phase = state.get('phase_id', '?')
    agent_name = state.get('agent_id', 'Agent')
    task_name = state.get('task_id', 'Task')

    if state.get("status") not in ["completed", "failed"]:
        agent_ui.markdown(f"**Phase {phase}** | 🤖 `{agent_name} - {task_name}`")
    
    # Image Rendering directly from the bucket
    image_to_show = None
    if state.get("status") == "completed":
        image_to_show = f"{state.get('job_id')}/final_illustration.png"
    elif state.get("status") not in ["failed", "completed"]:
        image_to_show = f"{state.get('job_id')}/latest.png"

    if image_to_show:
        try:
            img_bytes = bucket.blob(image_to_show).download_as_bytes()
            image_ui.image(img_bytes, use_container_width=True)
        except Exception:
            pass 

    # Logs
    logs = state.get("logs", [])
    if len(logs) > last_log_index:
        for i in range(last_log_index, len(logs)):
            log = logs[i]
            log_agent = log.get("agent_id", "Agent")
            
            if log_agent == "scaffolding_generator" and not log.get("response"):
                continue

            query = log.get("query", "No instruction provided.")
            response = log.get("response", {})
            
            entry_html = "<div class='entry'>"
            query_safe = str(query).replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            entry_html += f"<div class='instruction'><b>🤖 {log_agent} Task:</b><br>{query_safe}</div>"
            
            if isinstance(response, dict):
                for key, val in response.items():
                    val_safe = str(val).replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
                    entry_html += "<div style='margin-bottom: 8px; margin-left: 10px;'>"
                    entry_html += f"<span class='key'>{key}: </span>"
                    entry_html += f"<span class='val'>{val_safe}</span>"
                    entry_html += "</div>"
            else:
                val_safe = str(response).replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
                entry_html += f"<div class='val' style='margin-left: 10px;'>{val_safe}</div>"
                
            entry_html += "</div>"
            log_content += entry_html 
        
        last_log_index = len(logs)
        
        full_log_html = f"""
        <div style="height: 400px; overflow-y: auto; display: flex; flex-direction: column-reverse; background-color: #1E1E1E; border-radius: 8px; border: 1px solid #333;">
            <div style="padding: 15px; display: flex; flex-direction: column;">
                {log_content}
            </div>
        </div>
        """
        log_placeholder.markdown(full_log_html, unsafe_allow_html=True)
    
    return last_log_index, log_content


# --- MAIN LOGIC START ---
# Using the trigger flag instead of generate_btn directly
if st.session_state.trigger_job:
    st.session_state.trigger_job = False # Consume the trigger
    
    # Catch empty inputs gracefully without starting a job
    if not user_query:
        st.session_state.is_running = False
        st.warning("Please enter a Math Problem Description first.")
        st.rerun()

    st.session_state.job_id = str(uuid.uuid4())
    
    try:# --- CREATE FIRESTORE STUB WITH 7-DAY TTL ---
        doc_ref = db.collection("job_states").document(st.session_state.job_id)
        # Calculate exactly 7 days from right now in UTC
        expiry_date = datetime.now(timezone.utc) + timedelta(days=7)
        # Set the document with merge=True so we don't accidentally overwrite anything if a race condition occurs
        doc_ref.set({"ttl_expiry": expiry_date}, merge=True)
    except Exception as e:
        st.warning(f"Warning: Could not set database TTL: {e}")
    
    active_url = N8N_START_URL
    if env_choice == "Development (Test)":
        active_url = active_url.replace("/webhook/", "/webhook-test/")
    else:
        active_url = active_url.replace("/webhook-test/", "/webhook/")
    
    payload = {"query": user_query, "job_id": st.session_state.job_id, "style_preference": style_choice}
    try:
        start_req = requests.post(active_url, json=payload, headers={"x-api-key": API_KEY}, timeout=10)
        if start_req.status_code != 200:
            st.error("Failed to initiate pipeline.")
            st.session_state.is_running = False
            st.session_state.job_id = None # Prevents dropping into Scenario B on failure
            st.rerun() 
    except Exception as e:
        st.error(f"Error starting pipeline: {e}")
        st.session_state.is_running = False
        st.session_state.job_id = None # Prevents dropping into Scenario B on failure
        st.rerun()

# --- STATE MANAGEMENT & POLLING ---
if st.session_state.job_id:
    try:
        doc_ref = db.collection("job_states").document(st.session_state.job_id)
        
        # SCENARIO A: The Job is Actively Running
        if st.session_state.is_running:
            last_update_time = ""
            last_log_index = 0 
            log_content = "" 
            start_time = time.time() 
            
            status_ui.info("⏳ Pipeline initiated. Listening for agents...")
            
            while st.session_state.is_running:
                if time.time() - start_time > MAX_TIMEOUT:
                    status_ui.error(f"⌛ Pipeline Timeout: No completion received within {MAX_TIMEOUT}s.")
                    st.session_state.is_running = False
                    break

                time.sleep(1.5)
                doc = doc_ref.get()
                
                if doc.exists:
                    state = doc.to_dict()
                    if state.get("timestamp") == last_update_time:
                        continue
                    
                    last_update_time = state.get("timestamp")
                    start_time = time.time() 
                    
                    last_log_index, log_content = draw_ui_state(state, last_log_index, log_content)

                    # When finished, instantly drop out of the loop and re-enable the button
                    if state.get("status") in ["completed", "failed"]:
                        st.session_state.is_running = False
                        st.rerun()
        
        # SCENARIO B: The Job is Finished, but the page refreshed. 
        else:
            doc = doc_ref.get()
            if doc.exists:
                state = doc.to_dict()
                
                draw_ui_state(state, 0, "")
                
                if state.get("status") == "completed":
                    status_ui.success("✅ Generation Complete!")
                    # --- CAROUSEL UI ---
                    # We know we have these two images saved in the bucket
                    job_id = state.get("job_id")
                    available_images = [
                        {"path": f"{job_id}/scaffolding.png", "label": "Phase 2: Scaffolding Blueprint"},
                        {"path": f"{job_id}/final_illustration.png", "label": "Phase 6: Final Artwork"}
                    ]

                    # Safety check to ensure the index stays within bounds
                    st.session_state.carousel_idx = max(0,
                                                        min(st.session_state.carousel_idx, len(available_images) - 1))
                    current_img = available_images[st.session_state.carousel_idx]

                    # Render the active image (this instantly overwrites whatever draw_ui_state put there)
                    try:
                        img_bytes = bucket.blob(current_img["path"]).download_as_bytes()
                        image_ui.image(img_bytes, caption=current_img["label"], use_container_width=True)
                    except Exception:
                        image_ui.error("Image not found in storage.")

                        # Render Carousel Navigation Buttons (Subtle design)
                        col1, col2, col3, col4 = st.columns([4, 1, 1, 4])

                        with col2:
                            st.button("❮", on_click=prev_image, disabled=(st.session_state.carousel_idx == 0),
                                      use_container_width=True)
                        with col3:
                            st.button("❯", on_click=next_image,
                                      disabled=(st.session_state.carousel_idx == len(available_images) - 1),
                                      use_container_width=True)
                    # --- END CAROUSEL UI ---
                    if "user_message" in state:
                        with notes_ui.container():
                            st.markdown("### 📝 Notes")
                            st.info(state["user_message"])

                    zip_bundle = generate_zip_bundle(state)
                    download_ui.download_button("📦 Download Results (ZIP)", zip_bundle, file_name=f"EDEE_{st.session_state.job_id}.zip", mime="application/zip")
                
                elif state.get("status") == "failed":
                    status_ui.error(f"❌ Failed: {state.get('error_message')}")

    except Exception as e:
        st.error(f"Error during polling/rendering: {e}")
        st.session_state.is_running = False