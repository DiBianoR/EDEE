let INPUT_DATA;
let response;

// 1. Detect if the HTTP Node overwrote the JSON payload
if (items[0].json._phase_id !== undefined) {
    // Path A: We skipped the API call entirely, data is intact
    INPUT_DATA = items[0].json;
    response = INPUT_DATA._no_model_result;
} else {
    // Path B: The HTTP node executed and its output overwrote the item data.
    // We fetch the preserved inputs backwards from the Prepare Payload node.
    INPUT_DATA = $('1. Prepare Payload').item.json;
    response = items[0].json; // The Gemini response is the current item
}

// Safety check just in case response is completely empty
if (!response) response = {};

const CONFIG = INPUT_DATA.config;
const outputType = INPUT_DATA._output_type;
const PHASE_ID = INPUT_DATA._phase_id;
const AGENT_ID = INPUT_DATA._agent_id;
const TASK_ID = INPUT_DATA._task_id;
const taskInstruction = INPUT_DATA._task_instruction;
const requestBody = INPUT_DATA._request_body;
const fullHistory = INPUT_DATA._full_history || [];

let aiResult;
let finalImageBase64 = null;

// === 📦 PARSE RESPONSE ===
const content = typeof response === 'string' ? JSON.parse(response) : response;

if (outputType === "image_blob") {
    const part = content?.candidates?.[0]?.content?.parts?.[0];

    const inlineData = part?.inlineData || part?.inline_data;
    if (inlineData?.data) {
        finalImageBase64 = inlineData.data;
        aiResult = { status: "success", message: "Image generated successfully" };
    } else {
        let rawDump = part?.text || JSON.stringify(content);
        if (rawDump && rawDump.length > 500) rawDump = rawDump.substring(0, 500) + "...";
        aiResult = {
            status: "error",
            message: "No image found in response",
            raw_text: rawDump
        };
    }
} else {
    // Standard JSON Response
    if (content?.candidates && content.candidates[0]) {
        const rawText = content.candidates[0].content.parts[0].text;
        const cleanText = rawText.replace(/```json\n?|\n?```/g, "");
        try {
            aiResult = JSON.parse(cleanText);
        } catch(e) {
            // Fallback if the AI returned broken JSON
            aiResult = { raw_response: cleanText, parse_error: e.message };
        }
    } else {
        aiResult = content;
    }
}

// === 📤 CONSTRUCT OUTPUT ===
const stepRecord = {
  phase_id: PHASE_ID,
  agent_id: AGENT_ID,
  task_id: TASK_ID,
  task_instruction: taskInstruction
};

// Helper for Smart Truncation (Debug Logs)
function sanitize(key, value) {
    if (typeof value === "string" && value.length > 100) {
        if (key === "data" && (this.mimeType || this.mime_type)) return "<IMAGE_BLOB(Gemini)>";
        if (key === "base64_img_string") return "<IMAGE_BLOB>";
        if (key === "thoughtSignature") return "<THOUGHT_SIGNATURE>";
    }
    return value;
}

const sanitizedOutput = typeof aiResult === 'object'
    ? JSON.parse(JSON.stringify(aiResult, sanitize))
    : aiResult;

const sanitizedStepRecord = {
    ...stepRecord,
    output: sanitizedOutput          // ← thoughtSignature → <THOUGHT_SIGNATURE>
                                     // ← base64_img_string → <IMAGE_BLOB>
};

const outputData = {
    // 1. Parsed AI Response Fields
    // If aiResult is an object (standard JSON), we spread it so properties (reasoning, python_code, etc.) are top-level.
    ...(typeof aiResult === 'object' ? aiResult : { raw_response: aiResult }),

    // 2. Image Data (If present)
    ...(finalImageBase64 ? { base64_img_string: finalImageBase64, base64_img_string_mime: "image/jpg" } : {}),

    // 3. Context Preservation (Optional - Keep description alive if not updated)
    ...((INPUT_DATA.latest_description && (!aiResult || !aiResult.latest_description)) 
        ? { latest_description: INPUT_DATA.latest_description } 
        : {}),

    // 4. History & Config
    history: [...fullHistory, sanitizedStepRecord],
    config: CONFIG
};

// === 🛑 TERMINAL MODE DETECTION ===
// Check if this task is configured to cleanly terminate the pipeline
const taskConfig = CONFIG.phases[PHASE_ID].agents[AGENT_ID].tasks[TASK_ID];
const terminalConfig = taskConfig?.terminal_mode;
let broadcastStatus = "running";
let uiMessage = null;

if (terminalConfig) {
    broadcastStatus = terminalConfig.status || "failed";
    // Dynamically grab the message the AI wrote based on the configured key
    uiMessage = sanitizedOutput[terminalConfig.message_field];
}

// === 📡 GUI PROGRESS BROADCAST (Fire & Forget) ===
if (CONFIG.enable_gui_logging === true && CONFIG.gui_webhook_url) {
    try {
        const firestorePayload = {
            job_id: CONFIG.job_id, 
            phase_id: PHASE_ID,
            agent_id: AGENT_ID,
            task_id: TASK_ID,
            query: taskInstruction,
            response: sanitizedOutput,
            status: broadcastStatus, // "running", "failed", or "completed"
            timestamp: new Date().toISOString(),
            ...(finalImageBase64 ? { base64_img_string: finalImageBase64 } : {})  // Attach the image string if one was generated
        };

        // If this is a terminal agent, attach the message for the frontend
        if (terminalConfig && uiMessage) {
            // Streamlit looks for 'error_message' if status is failed, and 'user_message' if completed
            if (broadcastStatus === "failed") firestorePayload.error_message = uiMessage;
            else if (broadcastStatus === "completed") firestorePayload.user_message = uiMessage;
        }

        await this.helpers.httpRequest({
            method: 'POST',
            url: CONFIG.gui_webhook_url,
            headers: { 'Content-Type': 'application/json' },
            body: firestorePayload,
            json: true,
            timeout: 500 
        });
    } catch (e) {
        outputData.debug_gui_broadcast_error = e.message;
    }
}

return [{
    json: {
        outputData,
        model_url: INPUT_DATA._model_url,
        usage: content?.usageMetadata || {},
        grounding: content?.groundingMetadata || {}
    }
}];