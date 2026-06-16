// === 📥 RECEIVE FROM NODE 1 ===
// Reach-back target: must equal the EXACT n8n node name of universal_agent_1_of_4.
node1 = $("1. Prepare Payload").item.json; // ⚠️ confirm this matches your renamed node

config = node1.config;
const sessionState = node1.sessionState || {};
const sessionEvents = node1.sessionEvents || [];
model_url = node1.model_url;  // for Cost Calculator
noModelResult = node1.noModelResult
skipApi = node1.skipApi;
const agent_id = node1.targetAgentId || "Unknown Agent";
const task_id = node1.currentTaskId || "Unknown Task";
const outputType = node1.outputType || "text";

let geminiResponse = null;
if (items[0].json._skip_api === false) {
    geminiResponse = items[0].json;
}

// === 🧼 SANITIZER (keep gigantic blobs out of logs/state) ===
// JSON.stringify replacer: swaps long base64 / thought-signature strings for tags.
function sanitize(key, value) {
    if (typeof value === "string" && value.length > 100) {
        if (key === "data" && (this.mimeType || this.mime_type)) return "<IMAGE_BLOB(Gemini)>";
        if (key === "base64_img_string") return "<IMAGE_BLOB>";
        if (key === "thoughtSignature") return "<THOUGHT_SIGNATURE>";
    }
    return value;
}

// === 🔎 Parse the Output ===
//[WIP]]