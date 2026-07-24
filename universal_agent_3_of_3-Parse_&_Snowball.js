// === 🐞 DEBUG TOGGLE ===
// When true, the return envelope also carries debug_system / debug_prompt / debug_response:
// plain-text views of the final templated system instruction, user instruction, and raw
// response text ("<IMAGE_BLOB>" for image-only responses). Off for normal runs: these are
// top-level fields, so any downstream "All Except" passthrough into a universal-agent call
// would sweep them into session_state unless the cfg nodes exclude them too.
const DEBUG_EMIT_IO = true;

// === 📥 RECEIVE FROM NODE 1 ===
// Reach-back target: must equal the EXACT n8n node name of universal_agent_1_of_4.
const node1 = $("1. Prepare Payload").item.json; // ⚠️ confirm this matches your renamed node

const config = node1.config;
const sessionState = { ...(node1.sessionState || {}) };
const sessionEvents = node1.sessionEvents || [];
const resolvedProvider = node1.resolvedProvider;
const model_url = node1.model_url;  // for Cost Calculator
const noModelResult = node1.noModelResult;
const skipApi = node1.skipApi;
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
        if (key === "b64_json") return "<IMAGE_BLOB(OpenAI)>";
        if (key === "base64_img_string") return "<IMAGE_BLOB>";
        if (key === "thoughtSignature") return "<THOUGHT_SIGNATURE>";
    }
    return value;
}

// Truncation helper for throw messages (keeps the error string useful but bounded).
const trunc = (s) => (s && s.length > 500 ? s.substring(0, 500) + "..." : s);

// === 🔎 PARSE THE OUTPUT ===
// Job of this section: build the one turnEvent for this turn (parts kept structural,
// sanitized), merge any parsed JSON fields into sessionState, and pull the image out to
// the top-level return only.
//
// ERROR POLICY: any malformed response (missing image, missing text, broken JSON) THROWS.
// A model that failed to respond properly can't be recovered by the pipeline anyway — the
// retry loops handle SEMANTIC failures (failed QA, bad code), which arrive as perfectly
// well-formed responses. Throwing drops us into the catch sub-workflow, which owns all
// failure reporting to the UI (via log_error → report_unknown_error). Node 3 itself never
// broadcasts a failure. Throw messages carry agent/task, finishReason where available, and
// a truncated raw dump — once we throw, that string is the only diagnostic that survives.
let parsedResult = null;       // merged into sessionState; null on the image path
let eventParts = null;         // becomes this turn's turnEvent.parts
let finalImageBase64 = null;   // top-level return only - not sessionState/turnEvent
let finalImageBase64_mimeType = null;

if (skipApi) {
    // --- PATH A: no_model. Synthesize a Gemini-shaped part, as if constrained generation sent it. ---
    parsedResult = noModelResult;
    eventParts = [{ text: JSON.stringify(noModelResult) }];

} else if (outputType === "image_blob" && (resolvedProvider || "google") === "openai") {
    // --- PATH B2: OpenAI image edit (/v1/images/edits). Response shape:
    //     { created, data: [{ b64_json }], output_format, usage } — GPT image models always
    //     return base64 (no URL mode); output_format ("png"/"jpeg"/"webp") gives the mime subtype.
    const b64 = geminiResponse?.data?.[0]?.b64_json;

    if (!b64) {
        // OpenAI errors arrive as { error: { message, type, ... } } rather than a data array.
        const apiMessage = geminiResponse?.error?.message || "no error message";
        throw new Error(`[${agent_id} / ${task_id}] No image in OpenAI response (${apiMessage}) — raw: ${trunc(JSON.stringify(geminiResponse))}`);
    }

    finalImageBase64 = b64;
    finalImageBase64_mimeType = "image/" + (geminiResponse.output_format || "png");
    // Synthesize a Gemini-shaped parts array so the event log stays uniform across providers
    // (Node 1's replay/resolveLogPart logic only ever sees one shape).
    eventParts = [{ inlineData: { mimeType: finalImageBase64_mimeType, data: "<IMAGE_BLOB(OpenAI)>" } }];

} else if (outputType === "image_blob") {
    // --- PATH B: Gemini image generation. Keep Gemini's own parts array, sanitized. ---
    const rawParts = geminiResponse?.candidates?.[0]?.content?.parts || [];
    const inlineData = (rawParts[0]?.inlineData || rawParts[0]?.inline_data);

    if (!inlineData?.data) {
        // Gemini returns HTTP 200 with no image when it blocks on safety or truncates —
        // finishReason (or promptFeedback.blockReason) distinguishes "model declined" from a bug.
        const finishReason = geminiResponse?.candidates?.[0]?.finishReason
            || geminiResponse?.promptFeedback?.blockReason || "none";
        throw new Error(`[${agent_id} / ${task_id}] No image in Gemini response (finishReason: ${finishReason}) — raw: ${trunc(rawParts[0]?.text || JSON.stringify(geminiResponse))}`);
    }

    finalImageBase64 = inlineData.data;
    finalImageBase64_mimeType = inlineData.mimeType || inlineData.mime_type;
    eventParts = JSON.parse(JSON.stringify(rawParts, sanitize));  // sanitize() swaps the long `data` string for <IMAGE_BLOB(Gemini)>

} else {
    // --- PATH C: standard JSON text response. Keep the real parts array, sanitized; parse the text. ---
    const rawParts = geminiResponse?.candidates?.[0]?.content?.parts;
    const rawText = rawParts?.[0]?.text;

    if (rawText === undefined) {
        const finishReason = geminiResponse?.candidates?.[0]?.finishReason
            || geminiResponse?.promptFeedback?.blockReason || "none";
        throw new Error(`[${agent_id} / ${task_id}] No text in Gemini response (finishReason: ${finishReason}) — raw: ${trunc(JSON.stringify(geminiResponse))}`);
    }

    const cleanText = rawText.replace(/```json\n?|\n?```/g, "");
    try {
        parsedResult = JSON.parse(cleanText);
    } catch (e) {
        throw new Error(`[${agent_id} / ${task_id}] JSON parse failed (${e.message}) — raw: ${trunc(cleanText)}`);
    }
    // Keep the real (sanitized) parts array - preserves multi-part responses
    // (e.g. text + functionCall) exactly as Gemini emitted them.
    eventParts = JSON.parse(JSON.stringify(rawParts, sanitize));
}

// === 🏷️ MODEL NAME RESOLUTION ===
// Human-readable model name stamped onto this turn's response event.
// OpenAI carries it in the request body; Gemini only in the URL (".../models/NAME:generateContent"),
// so we take the segment after the last "/" and before the ":". no_model turns get "none".
const modelName = skipApi
    ? "none"
    : (node1.requestBody?.model
        || (model_url ? model_url.substring(model_url.lastIndexOf("/") + 1).split(":")[0] : null));

// === 🧱 RECORD THIS TURN AS AN EVENT ===
// ADK style: one immutable event per turn, appended to the log. `eventParts` was already
// built + sanitized in the parse section above (no_model → JSON text; image → provider
// parts with the blob swapped for <IMAGE_BLOB>; JSON text → the real sanitized parts).
// The raw image is deliberately NOT in here — it rides along top-level only, like a
// temp:/artifact. status is always "ok": error turns throw before reaching this point,
// so every recorded event is by definition a good one (mirrors Node 1's prompt events).
const turnEvent = {
    author: agent_id,
    task: task_id,
    status: "ok",
    parts: eventParts,
    // actions - turnEvent.actions = { state_delta: parsedResult }  //  not using for now
    // partial: false,  //  to detect incomplete content chunks during real-time streaming - not used atm
    timestamp: new Date().toISOString(),
    model: modelName  // cost is injected after this field by the cost calculator below
};

// Append-only: build a new array, never mutate the inherited log.
const updatedSessionEvents = [...sessionEvents, turnEvent];

// === 🧠 MERGE PARSED FIELDS INTO SESSION STATE ===
// sessionState is the live, full-fidelity working memory Node 1 reads for {variable}
// templating, so parsed fields (reasoning, latest_description, etc.) must land here.
// Only the no_model and standard-JSON paths produce parsedResult; the image path leaves
// it null and adds nothing.
// (Guard skips strings/arrays so a stray string result can't get spread into state as
//  character-indexed keys. no_model results are expected to be objects of named fields.)
//
// HOISTED FIELDS: a task may list result keys in `hoist_result_fields`. These are big,
// single-use payloads (e.g. python_code) that a downstream node reads directly off this
// turn's output but that no prompt templates via {var}. We lift them to the top-level
// return and delete them from state, so they ride ONE hop to their consumer instead of
// being copied into every subsequent envelope's session_state. Same treatment as the
// image blob. The reviewers still see the value via scoped history (session_events).
const hoistKeys = config.tasks[task_id]?.hoist_result_fields || [];
const hoistedFields = {};
if (parsedResult && typeof parsedResult === "object" && !Array.isArray(parsedResult)) {
    Object.assign(sessionState, parsedResult);
    for (const k of hoistKeys) {
        if (k in sessionState) {
            hoistedFields[k] = sessionState[k];
            delete sessionState[k];
        }
    }
}

// === 🛑 TERMINAL MODE DETECTION ===
// Check if this task is configured to cleanly terminate the pipeline (success or failure).
// NOTE: terminal_mode is for PLANNED endings where an agent successfully wrote a message
// for the user. UNPLANNED endings (malformed responses) throw in the parse section above
// and are handled by the catch sub-workflow instead.
const terminalConfig = config.tasks[task_id]?.terminal_mode;
let jobStatus = "running";   // job-level pipeline status: "running" | "failed" | "completed"
let uiMessage = null;        // terminal agent's user-facing text (error_message / user_message)

if (terminalConfig) {  // has fields status & message_field
    jobStatus = terminalConfig.status || "failed";  // status[to GUI:failed/completed]
    uiMessage = parsedResult?.[terminalConfig.message_field];  // message_field[name of the field w/ user-facing message]
}

// === 📡 GUI PROGRESS BROADCAST (Fire & Forget) ===
// Keeps the ORIGINAL broadcast payload shape so the state-manager + Streamlit frontend keep
// working unchanged. Fields the flattened/ADK rewrite no longer carries get stand-ins:
//   - phase_id: "-"  (phases were flattened out of the config; the GUI just renders "Phase -")
//   - query:    falls back to the untemplated task instruction
// Only successful turns reach this point (error turns threw above), so `response` is
// either the sanitized parsed dict or the synthetic image-success object.
if (config.enable_gui_logging === true && config.gui_webhook_url) {

    let broadcastResponse;
    if (parsedResult && typeof parsedResult === "object") {
        broadcastResponse = JSON.parse(JSON.stringify(parsedResult, sanitize));
    } else if (finalImageBase64) {
        broadcastResponse = { status: "success", message: "Image generated successfully" };
    } else {
        broadcastResponse = parsedResult; // defensive fallback (not expected in practice)
    }

    // query: prefer the templated instruction if Node 1 forwards it, else the raw
    const broadcastQuery = node1.finalUserInstruction || config.tasks[task_id]?.instruction || "";

    try {
        const broadcastPayload = {
            job_id: config.job_id,
            phase_id: "-",                 // dummy: no phase in the flattened config
            agent_id: agent_id,
            task_id: task_id,
            query: broadcastQuery,
            response: broadcastResponse,
            status: jobStatus, // "running", "failed", or "completed"
            timestamp: new Date().toISOString(),
            ...(finalImageBase64 ? { base64_img_string: finalImageBase64 } : {})  // Attach the image string if one was generated
        };

        // If this is a terminal agent, attach the message for the frontend
        if (terminalConfig && uiMessage) {
            // Streamlit looks for 'error_message' if status is failed, and 'user_message' if completed
            if (jobStatus === "failed") broadcastPayload.error_message = uiMessage;
            else if (jobStatus === "completed") broadcastPayload.user_message = uiMessage;
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
        // Fire-and-forget: never let a GUI logging failure break the pipeline.
        // Not forwarded downstream (would land in next-turn sessionState); logged locally instead.
        console.log(`GUI broadcast failed: ${e.message}`);
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
        ? { base64_img_string: finalImageBase64, base64_img_string_mime: finalImageBase64_mimeType || "image/png" }
        : {}),

    // Hoisted heavy fields (e.g. python_code): top-level, single-hop, never in state.
    ...hoistedFields,

    // Debug I/O record: plain-text views of this turn's exchange.
    //   debug_system   — final templated systemInstruction text (null if agent has none)
    //   debug_prompt   — final templated user instruction (current turn only, no history)
    //   debug_response — raw response text/JSON; "<IMAGE_BLOB>" if the model returned
    //                    only an image with no accompanying text
    ...(DEBUG_EMIT_IO
        ? {
            debug_system: node1.requestBody?.systemInstruction?.parts?.[0]?.text || null,
            debug_prompt: node1.finalUserInstruction || null,
            debug_response: skipApi
                ? JSON.stringify(noModelResult)
                : (geminiResponse?.candidates?.[0]?.content?.parts?.find(p => p.text !== undefined)?.text
                    ?? (finalImageBase64 ? "<IMAGE_BLOB>" : null))
          }
        : {}),
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
  },
  // --- OpenAI image models (keyed by MODEL NAME, not URL: all tiers share
  //     /v1/images/edits; lookup falls back to requestBody.model below). Rates
  //     are $/1M tokens split by modality, matching the usage.*_tokens_details shape. ---
  "gpt-image-1.5": {
    input_text: 5.00,
    input_image: 8.00,
    output_text: 10.00,
    output_image: 32.00
  },
  "gpt-image-1-mini": {
    input_text: 2.00,
    input_image: 2.50,   // ⚠️ verify mini rates against the live pricing page before relying on them
    output_text: 8.00,
    output_image: 8.00
  }
};

// --- EXECUTION ENGINE ---
// Inlined I/O: read locals directly instead of a downstream node's $input payload.
// (outputData was assembled above; model_url/usage/grounding are already in scope.)
const modelUrl = model_url;
const requestModel = node1.requestBody?.model || null;  // OpenAI carries the model in the body, not the URL
const usage = geminiResponse?.usageMetadata || {};
const grounding = geminiResponse?.groundingMetadata || {};

let taskCost = 0;
const pricing = cost_registry[modelUrl] || (requestModel ? cost_registry[requestModel] : undefined);

if (pricing && geminiResponse?.usage?.input_tokens_details) {  // OpenAI
    // --- OpenAI images usage shape: flat modality splits, no per-detail arrays,
    //     no context-window threshold, no grounding fees on this endpoint. ---
    const u = geminiResponse.usage;
    const inText  = u.input_tokens_details?.text_tokens ?? 0;
    const inImage = u.input_tokens_details?.image_tokens ?? 0;
    const outText  = u.output_tokens_details?.text_tokens ?? 0;
    const outImage = u.output_tokens_details?.image_tokens ?? (u.output_tokens ?? 0);

    taskCost = (inText  / 1000000) * (pricing.input_text   ?? 0)
             + (inImage / 1000000) * (pricing.input_image  ?? 0)
             + (outText  / 1000000) * (pricing.output_text  ?? 0)
             + (outImage / 1000000) * (pricing.output_image ?? 0);

} else if (pricing) {  // Gemini
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
return [{ json: outputData }];