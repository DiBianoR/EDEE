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
// Job of this section: build the one turnEvent for this turn (parts kept structural,
// sanitized), merge any parsed JSON fields into sessionState, pull the image out to the
// top-level return only, and set the status/statusMessage reroute gate.
let parsedResult = null;       // merged into sessionState; null on image/error paths
let eventParts = null;         // becomes this turn's turnEvent.parts
let finalImageBase64 = null;   // top-level return only - not sessionState/turnEvent
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

// === 🧱 RECORD THIS TURN AS AN EVENT ===
// ADK style: one immutable event per turn, appended to the log. `eventParts` was already
// built + sanitized in the parse section above (no_model → JSON text; image → Gemini parts
// with the blob swapped for <IMAGE_BLOB>; JSON text → the real sanitized parts). The raw
// image is deliberately NOT in here — it rides along top-level only, like a temp:/artifact.
const turnEvent = {
    author: agent_id,
    task: task_id,
    status: status,
    parts: eventParts,
    // actions - turnEvent.actions = { state_delta: parsedResult }  //  not using for now
    // partial: false,  //  to detect incomplete content chunks during real-time streaming - not used atm
    timestamp: new Date().toISOString()
};

// Append-only: build a new array, never mutate the inherited log.
const updatedSessionEvents = [...sessionEvents, turnEvent];

// === 🧠 MERGE PARSED FIELDS INTO SESSION STATE ===
// sessionState is the live, full-fidelity working memory Node 1 reads for {variable}
// templating, so parsed fields (reasoning, python_code, latest_description, etc.) must land
// here. Only the no_model and standard-JSON paths produce parsedResult; image and error
// paths leave it null and add nothing.
// (Guard skips strings/arrays so a stray string result can't get spread into state as
//  character-indexed keys. no_model results are expected to be objects of named fields.)
if (parsedResult && typeof parsedResult === "object" && !Array.isArray(parsedResult)) {
    Object.assign(sessionState, parsedResult);
}

// === 🛑 TERMINAL MODE DETECTION ===
// Check if this task is configured to cleanly terminate the pipeline (success or failure).
// NOTE: lookup path changed with the flattened config. The old file read
// CONFIG.phases[PHASE_ID].agents[AGENT_ID].tasks[TASK_ID].terminal_mode; Node 1's registry
// contract is config.tasks[task_id], so terminal_mode now lives directly under the task.
const terminalConfig = config.tasks[task_id]?.terminal_mode;
let broadcastStatus = "running";   // GUI pipeline status: "running" | "failed" | "completed"
                                   // (distinct from `status` above, which is parse "ok"/"error")
let uiMessage = null;              // terminal agent's user-facing text (error_message / user_message)

if (terminalConfig) {  // has fields status & message_field
    broadcastStatus = terminalConfig.status || "failed";  // status[to GUI:failed/completed]
    uiMessage = parsedResult?.[terminalConfig.message_field];  // message_field[name of the field w/ user-facing message]
}

// === 📡 GUI PROGRESS BROADCAST (Fire & Forget) ===
// Keeps the ORIGINAL broadcast payload shape so the state-manager + Streamlit frontend keep
// working unchanged. Fields the flattened/ADK rewrite no longer carries get stand-ins:
//   - phase_id: "-"  (phases were flattened out of the config; the GUI just renders "Phase -")
//   - query:    falls back to the untemplated task instruction, since Node 1 doesn't yet
//               forward the templated finalUserInstruction (see hand-off note below).
let guiBroadcastError = null; // surfaced by the final return section (old: outputData.debug_gui_broadcast_error)

if (config.enable_gui_logging === true && config.gui_webhook_url) {

    // Rebuild the old `sanitizedOutput` the GUI expects as `response`: the parsed dict with any
    // long base64/thoughtSignature strings swapped out (reusing sanitize() from the parse
    // section). parsedResult is null on the image + error paths, so mirror the old file's
    // synthetic objects for those. (Error objects carry `message` rather than the old
    // raw_text/raw_response, but statusMessage already holds the useful detail.)
    let broadcastResponse;
    if (parsedResult && typeof parsedResult === "object") {
        broadcastResponse = JSON.parse(JSON.stringify(parsedResult, sanitize));
    } else if (finalImageBase64) {
        broadcastResponse = { status: "success", message: "Image generated successfully" };
    } else if (status === "error") {
        broadcastResponse = { status: "error", message: statusMessage };
    } else {
        broadcastResponse = parsedResult; // null passthrough (not expected in practice)
    }

    // query: prefer the templated instruction if a future Node 1 forwards it, else the raw
    // config instruction. Placeholder until the Node 1 hand-off (below) is wired up.
    const broadcastQuery = node1.finalUserInstruction || config.tasks[task_id]?.instruction || "";

    try {
        const broadcastPayload = {
            job_id: config.job_id,
            phase_id: "-",                 // dummy: no phase in the flattened config
            agent_id: agent_id,
            task_id: task_id,
            query: broadcastQuery,
            response: broadcastResponse,
            status: broadcastStatus, // "running", "failed", or "completed"
            timestamp: new Date().toISOString(),
            ...(finalImageBase64 ? { base64_img_string: finalImageBase64 } : {})  // Attach the image string if one was generated
        };

        // If this is a terminal agent, attach the message for the frontend
        if (terminalConfig && uiMessage) {
            // Streamlit looks for 'error_message' if status is failed, and 'user_message' if completed
            if (broadcastStatus === "failed") broadcastPayload.error_message = uiMessage;
            else if (broadcastStatus === "completed") broadcastPayload.user_message = uiMessage;
        }

        await this.helpers.httpRequest({
            method: 'POST',
            url: config.gui_webhook_url,
            headers: { 'Content-Type': 'application/json' },
            body: broadcastPayload,
            json: true,
            timeout: 500
        });
    } catch (e) {
        // Fire-and-forget: never let a GUI logging failure break the pipeline. Stash the
        // message so the final return section can surface it.
        guiBroadcastError = e.message;
    }
}

// === 📦 ASSEMBLE OUTPUT ENVELOPE ===
// The state envelope returned to the parent orchestrator, which threads it into the next
// sub-workflow call. Mirrors the old `outputData` role, but ADK-shaped:
//   old `history`            → session_events (the append-only log built this turn)
//   old spread parsed fields → session_state  (already merged via Object.assign above; the
//                              next Node 1 reads {variables} from here, so they no longer
//                              need to be spread at top level)
//   old latest_description   → dropped: persistent session_state carries it forward on its
//                              own, so the old "keep it alive if not updated" hack is obsolete
// The raw image rides at top level as base64_img_string (NOT inside the log/state) so the
// next agent's Node 1 can attach it as an input image, exactly as the old file did.
const outputData = {
    config: config,
    session_state: sessionState,
    session_events: updatedSessionEvents,

    // Image passthrough (top-level only; kept out of session_state & session_events).
    // mime is required: Node 1 only attaches the image when BOTH string + mime are present.
    ...(finalImageBase64
        ? { base64_img_string: finalImageBase64, base64_img_string_mime: "image/jpg" }
        : {}),

    // GUI broadcast failure surfaced for debugging (old: outputData.debug_gui_broadcast_error)
    ...(guiBroadcastError ? { debug_gui_broadcast_error: guiBroadcastError } : {})
};


// ============================================================================
// 💰 COST CALCULATION  (inlined from universal_agent_4_of_4_Cost_Calculator.js)
// Appended here instead of running as a separate downstream node so the raw image
// blob never has to survive a sub-workflow hop. Calculations below are unchanged.
// ============================================================================

// use with usageMetadata & groundingMetadata to calculate costs
const cost_registry = {
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent": {
    input: 2.00,
    output_text: 12.00,
    contextThreshold: 200000,
    input_over_threshold: 4.00,
    output_text_over_threshold: 18.00,
    cache_read: 0.20,
    cache_read_over_threshold: 0.40,
    cache_storage_hourly: 4.50,
    grounding_search_per_1k: 14.00,  // A customer-submitted request to Gemini may result in one or more queries to Google Search. You will be charged for each individual search query performed.
    grounding_maps_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent": {
    input: 1.50,
    output_text: 9.00,
    cache_read: 0.15,
    cache_storage_hourly: 1.00,
    grounding_search_per_1k: 14.00,  // A customer-submitted request to Gemini may result in one or more queries to Google Search. You will be charged for each individual search query performed.
    grounding_maps_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent": {
    input_default: 0.25, // text, image, video
    input_audio: 0.50,   // double rate for audio
    output_text: 1.50,
    cache_read_default: 0.025,
    cache_read_audio: 0.05,
    cache_storage_hourly: 1.00,
    grounding_search_per_1k: 14.00,
    grounding_maps_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image:generateContent": {
    // Inherits base text/image properties from Gemini 3.1 Pro
    input: 2.00,
    output_text: 12.00,
    output_image: 120.00,
    grounding_search_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image:generateContent": {
    input: 0.50,
    output_text: 3.00,
    output_image: 60.00,
    grounding_search_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent": {
    input: 0.30,
    output_text: 2.50,   // Matched to base 2.5 Flash text output rate
    output_image: 30.00
  }
};

// --- EXECUTION ENGINE ---
// Inlined I/O: read locals directly instead of a downstream node's $input payload.
// (outputData was assembled above; model_url/usage/grounding are already in scope.)
const modelUrl = model_url;
const usage = geminiResponse?.usageMetadata || {};
const grounding = geminiResponse?.groundingMetadata || {};

let taskCost = 0;
const pricing = cost_registry[modelUrl];

if (pricing) {
    const totalPromptTokens = usage.promptTokenCount ?? 0;
    const totalTokens = usage.totalTokenCount ?? 0;

    // Evaluate Context Window Threshold
    const threshold = pricing.contextThreshold ?? 0;
    const isOverThreshold = threshold > 0 && totalTokens > threshold;

    // Calculate Input Tokens
    let inputCost = 0;
    const promptDetails = usage.promptTokensDetails || [{ modality: "TEXT", tokenCount: totalPromptTokens }];

    for (const detail of promptDetails) {
        const modality = (detail.modality ?? "TEXT").toLowerCase();
        const tokenCount = detail.tokenCount ?? 0;
        if (tokenCount === 0) continue;

        let baseInputRate = null;
        if (pricing.input !== undefined) {
            baseInputRate = pricing.input;
        } else if (pricing[`input_${modality}`] !== undefined) {
            baseInputRate = pricing[`input_${modality}`];
        } else if (pricing.input_default !== undefined) {
            baseInputRate = pricing.input_default;
        } else {
            throw new Error(`Billing Error: No input rate found for model ${modelUrl} (Modality: ${modality})`);
        }

        if (isOverThreshold) {
            baseInputRate = pricing.input_over_threshold ?? (baseInputRate * 2);
        }
        inputCost += (tokenCount / 1000000) * baseInputRate;
    }

    // Calculate Output Tokens
    let outputCost = 0;
    const candidateDetails = usage.candidatesTokensDetails || [{ modality: "TEXT", tokenCount: usage.candidatesTokenCount ?? 0 }];

    for (const detail of candidateDetails) {
        const modality = (detail.modality ?? "TEXT").toLowerCase();
        const tokenCount = detail.tokenCount ?? 0;
        if (tokenCount === 0) continue;

        let baseOutputRate = 0;
        if (modality === "text") {
            baseOutputRate = pricing.output_text ?? 0;
            if (isOverThreshold) {
                baseOutputRate = pricing.output_text_over_threshold ?? (baseOutputRate * 2);
            }
        } else if (modality === "image") {
            baseOutputRate = pricing.output_image ?? 0;
        } else {
            baseOutputRate = pricing[`output_${modality}`] ?? pricing.output_text ?? 0;
        }

        outputCost += (tokenCount / 1000000) * baseOutputRate;
    }

    // Calculate Grounding Search Fees
    let groundingCost = 0;
    if (grounding.webSearchQueries && Array.isArray(grounding.webSearchQueries)) {
        const searchQueriesCount = grounding.webSearchQueries.length;
        const searchRatePer1k = pricing.grounding_search_per_1k ?? 0;
        groundingCost = (searchQueriesCount / 1000) * searchRatePer1k;
    }

    taskCost = inputCost + outputCost + groundingCost;
}

// 2. Inject ONLY the final cost into the most recent record.
//    (old target: outputData.history[last]; new target: this turn's session_event)
if (outputData.session_events && outputData.session_events.length > 0) {
    outputData.session_events[outputData.session_events.length - 1].cost = Number(taskCost.toFixed(6));
}

// 3. Return ONLY the outputData. Billing metadata (modelUrl/usage/grounding) stays local
//    and is never bundled into the return, so nothing extra crosses the sub-workflow boundary.
return { json: outputData };