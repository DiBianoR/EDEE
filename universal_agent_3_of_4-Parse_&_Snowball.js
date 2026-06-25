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
if (!skipApi) {
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

// === 🔎 PARSE THE OUTPUT ===
// Job of this section: build the one sessionEvent for this turn (parts kept structural,
// sanitized), merge any parsed JSON fields into sessionState, pull the image out to the
// top-level return only, and set the status/statusMessage reroute gate.
let parsedResult = null;       // merged into sessionState; null on image/error paths
let eventParts = null;         // becomes this turn's sessionEvent.parts
let finalImageBase64 = null;   // top-level return only - not sessionState/sessionEvent
let status = "ok";
let statusMessage = null;

if (skipApi) {
    // --- PATH A: no_model. Synthesize a Gemini-shaped part, as if constrained generation sent it. ---
    parsedResult = noModelResult;
    eventParts = [{ text: JSON.stringify(noModelResult) }];

} else if (outputType === "image_blob") {
    // --- PATH B: image generation. Keep Gemini's own parts array, sanitized. ---
    const rawParts = geminiResponse?.candidates?.[0]?.content?.parts || [];
    const inlineData = (rawParts[0]?.inlineData || rawParts[0]?.inline_data);

    if (inlineData?.data) {
        finalImageBase64 = inlineData.data;
        eventParts = JSON.parse(JSON.stringify(rawParts, sanitize));  // sanitize() swaps the long `data` string for <IMAGE_BLOB(Gemini)>
    } else {
        status = "error";
        statusMessage = "No image found in response";
        let rawDump = rawParts[0]?.text || JSON.stringify(geminiResponse);
        if (rawDump && rawDump.length > 500) rawDump = rawDump.substring(0, 500) + "...";
        eventParts = [{ text: `${status}: ${statusMessage}\nraw_result: ${rawDump}` }];
    }

} else {
    // --- PATH C: standard JSON text response. Keep the real parts array, sanitized; parse the text. ---
    const rawParts = geminiResponse?.candidates?.[0]?.content?.parts;
    const rawText = rawParts?.[0]?.text;

    if (rawText === undefined) {
        status = "error";
        statusMessage = "No text found in response";
        eventParts = [{ text: `${status}: ${statusMessage}\nraw_result: ${JSON.stringify(geminiResponse).substring(0, 500)}` }];
    } else {
        const cleanText = rawText.replace(/```json\n?|\n?```/g, "");
        try {
            parsedResult = JSON.parse(cleanText);
            // Keep the real (sanitized) parts array - preserves multi-part responses
            // (e.g. text + functionCall) exactly as Gemini emitted them.
            eventParts = JSON.parse(JSON.stringify(rawParts, sanitize));
        } catch (e) {
            status = "error";
            statusMessage = `JSON parse failed: ${e.message}`;
            let rawDump = cleanText.length > 500 ? cleanText.substring(0, 500) + "..." : cleanText;
            eventParts = [{ text: `${status}: ${statusMessage}\nraw_result: ${rawDump}` }];
        }
    }
}

// Surface a readable error in sessionState too, so a later agent (e.g. the troubleshooter)
// can see what went wrong without parsing the transcript.
if (status === "error") {
    sessionState.last_error = statusMessage;
}

//[WIP]]