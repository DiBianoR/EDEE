// === ⚙️ CONFIGURATION ===
const inputData = items[0].json;
const {
    config,
    session_state: sessionState = {},  // state: contains a flat dictionary of misc. variables
    session_events: sessionEvents = [],  // history: contains list of {author, task, parts[text, inlineData, fileData, functionCall, functionResponse], actions[], partial, timestamp} objs
    prompt_author: promptAuthor = "system",  // used if another agent directly wrote the prompt
    PHASE_ID: currentPhaseId,
    TASK_ID: currentTaskId,
    AGENT_ID: providedAgentId,
    base64_img_string,  // If we have an input image
    base64_img_string_mime,
	debug_system,
	debug_prompt,
	debug_response,
    ...externalVars
} = inputData;
Object.assign(sessionState, externalVars);  // Other variables into sessionState. If same name exists, overwritten.
if (!config) throw new Error("CONFIGURATION ERROR: No 'config' object found in the payload.");
if (!config.tasks || !config.agents) throw new Error("CONFIGURATION ERROR: The provided 'config' is missing 'tasks' or 'agents' registries.");
// job_id names the bucket folder every artifact of this run lands in, so a blank one
// scatters results instead of failing outright. Reject it before any work happens.
if (!config.job_id || !String(config.job_id).trim()) throw new Error("CONFIGURATION ERROR: config.job_id is empty — the run was launched without a job id.");

// agent & task
if (!currentTaskId) throw new Error("No TASK_ID provided to the Universal Agent.");
const taskBlueprint = config.tasks[currentTaskId];
if (!taskBlueprint) throw new Error(`Task ID '${currentTaskId}' not found in config.`);
const targetAgentId = providedAgentId || taskBlueprint.assigned_agent;
const agentBlueprint = config.agents[targetAgentId];
if (!agentBlueprint) throw new Error(`Agent ID '${targetAgentId}' not found in config.`);

// === 🎯 MODEL CAPABILITY IDENTIFICATION ===
const modelType = taskBlueprint.model_type || agentBlueprint.model_type || "text"; // Supports "text", "view_img", "img2img"
const isImageRead = (modelType === "view_img") || (modelType === "img2img");
const isImageGen = (modelType === "img2img");
const outputType = isImageGen ? "image_blob" : "json";  //  constrained generation produces json, no raw text case atm
const resolvedProvider = taskBlueprint.provider || agentBlueprint.provider ||
    config.provider_by_type[modelType] || "google";

// === 🪶 String Templating (The ADK Way) ===
function templateInstruction(instruction, state) {
    if (!instruction) return "";
    return instruction.replace(/{([^}]+)}/g, (match, rawKey) => {
        const key = rawKey.trim();
        let val = state[key];
        if (val === undefined) throw new Error(`TEMPLATE ERROR: The variable '{${key}}' was referenced in the prompt, but it does not exist in the session_state.`);
        if (typeof val === 'object') return JSON.stringify(val);  // If the variable is an array/object (like {master_table}), we stringify it so it renders as text in the prompt.
        return val;
    });
}

// Recursively template the string leaves of a config value (used by no_model results).
// Strings get {var} substitution; objects/arrays recurse; numbers/booleans/null pass through.
function templateObject(value, state) {
    if (typeof value === 'string') return templateInstruction(value, state);
    if (Array.isArray(value)) return value.map(v => templateObject(v, state));
    if (value && typeof value === 'object') {
        const out = {};
        for (const [k, v] of Object.entries(value)) out[k] = templateObject(v, state);
        return out;
    }
    return value;
}

// === 📝 CONSTRUCT SYSTEM & USER PROMPTS ===
const finalSystemInstruction = templateInstruction(agentBlueprint.system_identity, sessionState);
const finalUserInstruction = templateInstruction(taskBlueprint.instruction, sessionState);

// === 📦 Construct empty API Payload ===
let requestBody = {
    contents: []  // contains list of {role, parts[text, inlineData, fileData, functionCall, functionResponse]} objs
};  // systemInstruction{}, generationConfig{} & safetySettings[] are the other optional fields
if (finalSystemInstruction) {
    requestBody.systemInstruction = { parts: [{ text: finalSystemInstruction }] };
}
if (outputType === "json" && taskBlueprint.schema) {  // constrained generation - json schema
    requestBody.generationConfig = {
        response_mime_type: "application/json",
        response_schema: taskBlueprint.schema
    };
}

// === 📜 HISTORY SCOPE RESOLUTION & CONSTRUCTION ===
const rawHistoryScope = taskBlueprint.history_scope || agentBlueprint.history_scope || [];  // A list of agent IDs
const historyScope = Array.isArray(rawHistoryScope) ? rawHistoryScope : [rawHistoryScope];  // Allow single item as string

// Response events: in scope iff their author is listed (unchanged behavior).
// Prompt events (author "user"/"system") are paired with the event immediately after
// them in the log (their reply). A prompt is replayed iff its reply survived the filter
// AND one of these holds:
//   a) the reply is the CURRENT agent's own — always include, even when "system" isn't
//      in the scope list. This keeps the agent's past model turns properly paired with
//      the instructions that elicited them (ADK replays user prompts verbatim; without
//      this, self-history replays as unprompted answers).
//   b) the prompt's author is explicitly in scope (the original opt-in rule for
//      replaying other conversations' prompts).
// Prompts whose turn crashed (successor missing or itself a prompt) are excluded.
// Scoped-out prompts leave adjacent same-role replies, which flushQueue already squashes.
const PROMPT_AUTHORS = ["user", "system"];
const filteredEvents = sessionEvents.filter((event, i) => {
    if (PROMPT_AUTHORS.includes(event.author)) {
        const next = sessionEvents[i + 1];
        const replyInScope = !!next && historyScope.includes(next.author) && !PROMPT_AUTHORS.includes(next.author);
        if (!replyInScope) return false;
        return next.author === targetAgentId || historyScope.includes(event.author);
    }
    return historyScope.includes(event.author);
});

// === 🖼️️️ FINISH CONSTRUCTING CURRENT PROMPT ===
const currentParts = [{ text: finalUserInstruction }];
if (isImageRead && base64_img_string && base64_img_string_mime) {
    currentParts.unshift({ // unshift adds the element to the front of the array
        inlineData: {
            mimeType: base64_img_string_mime,
            data: base64_img_string
        }
    });
} else if (isImageRead) {
    // An image-capable task ran with no image attached. Historically this failed
    // SILENTLY and the model confabulated detailed reviews of images it never saw. Prefix a
    // marker so the model won't hallucinate — and so the logged prompt event makes
    // the missing attachment obvious in the session log / debug viewer.
    // Wording differs by type: for img2img an absent input can be a legitimate mode
    // (DIRECT_IMAGE_GEN draws from text alone), and the artist's instruction tells
    // it to DRAW an error message when its briefing is missing — a "warning" here
    // could trigger that, so img2img gets a neutral note instead.
    currentParts[0].text =
        (isImageGen ? "[note: no input image attached — generate from the text description alone]"
                    : "[warning: no image attached to this vision request]")
        + "\n\n" + currentParts[0].text;
}
const currentPromptEvent = {
    author: promptAuthor,
    parts: currentParts
};

// === 🔄 QUEUE PROCESSING & CONTENT FLUSHING ===
const eventsToProcess = [...filteredEvents, currentPromptEvent];
const finalContents = [];
let queue = [];
let currentIsModel = null; // true if targetAgentId (model), false otherwise (user)

// Parts pulled from the LOG are sanitized: any inlineData.data is a placeholder tag
// (<IMAGE_BLOB...>), not real base64. Replaying that verbatim would send Gemini an invalid
// blob, so we rewrite the part into a plain text marker instead. Returning a {text} part
// (rather than handling this at push time) lets the marker fall through the normal text
// path below, so it inherits author labeling like any other text - otherwise an image-only
// event (common for img2img responses, which carry no text part) would replay unattributed.
// Live blobs never reach here: they only enter via currentPromptEvent, which is unsanitized.
const resolveLogPart = (part, isModelPart) => {
    const blob = part.inlineData || part.inline_data;
    if (blob && typeof blob.data === "string" && blob.data.startsWith("<IMAGE_BLOB")) {
        return { text: isModelPart ? "[image generated here - omitted from history]"
                                   : "[image omitted from history]" };
    }
    return part; // text, functionCall, functionResponse, fileData, live inlineData: untouched
};

// n8n persists the event log as JSON, so a logged model reply is one JSON *string* — every
// newline inside its field values escaped to the two-character sequence \n. The LLM must
// never see that escaping: whenever a replayed text part parses as a JSON object/array,
// re-render it as readable "key: value" text with real newlines restored. Anything that
// isn't pure JSON (live instructions, image markers, fenced text) fails the shape check
// or the parse and passes through verbatim.
const renderJsonValue = (val) => {
    if (typeof val === "string") return val;                              // raw — real newlines
    if (val === null || typeof val !== "object") return String(val);
    if (Array.isArray(val)) return val.map(renderJsonValue).join("\n");   // separator BETWEEN items only: 1 item ⇒ no newline
    return Object.entries(val).map(([k, v]) => {
        const r = renderJsonValue(v);
        return r.includes("\n") ? `${k}:\n${r}` : `${k}: ${r}`;           // multi-line values start under their key
    }).join("\n");
};
const restoreJsonText = (text) => {
    if (typeof text !== "string") return text;
    const t = text.trim();
    const looksJson = (t.startsWith("{") && t.endsWith("}")) || (t.startsWith("[") && t.endsWith("]"));
    if (!looksJson) return text;
    try { return renderJsonValue(JSON.parse(t)); } catch (e) { return text; }
};

const flushQueue = () => {
    if (queue.length === 0) return;

    const isModelQueue = currentIsModel;
    const role = isModelQueue ? "model" : "user";
    const contentParts = [];

    // Determine if we need to apply author labels based on your rules
    let applyLabels = false;
    if (!isModelQueue) {
        const uniqueAuthors = new Set(queue.map(e => e.author));

        if (queue.length === 1) {
            const author = queue[0].author;
            // Single item: apply if author isn't a standard 'system' or 'user'
            applyLabels = author !== "system" && author !== "user";
        } else {
            const allSame = uniqueAuthors.size === 1;
            const onlyAuthor = Array.from(uniqueAuthors)[0];
            // Multiple items: apply unless ALL are 'system' or ALL are 'user'
            applyLabels = !(allSame && (onlyAuthor === "system" || onlyAuthor === "user"));
        }
    }

    // Process the queue items into standard Gemini parts
    let pushedAnyText = false; // across the queue: gates separators so the FIRST item never gets a leading newline
    queue.forEach((event) => {
        // The LIVE self-prompt stays unlabeled: the agent doesn't reliably know its own
        // registry name, so "[<own id>] said:" would read as a third party's words. This
        // forfeits the label-as-boundary for this one event, which is acceptable only
        // because it is always the FINAL event of the request — nothing follows it to
        // need delimiting. Replayed copies in later turns are unaffected (they surface
        // as model turns, which are never labeled anyway).
        const suppressLabel = event === currentPromptEvent && event.author === targetAgentId;
        let firstTextOfEvent = true;
        event.parts.forEach((rawPart) => {
            // Swap sanitized image placeholders for text BEFORE the text/non-text split,
            // so markers get labeled and prefixed exactly like real text.
            const part = resolveLogPart(rawPart, isModelQueue);
            if (part.text !== undefined) {
                // Restore logged JSON to readable text (real newlines) before the LLM sees it.
                let text = restoreJsonText(part.text);
                if (firstTextOfEvent) {
                    // ADK-style bracketed attribution, task-qualified so an agent that ran
                    // several tasks — or the same task across a retry loop — replays as
                    // distinguishable events. (The live prompt event carries no task field
                    // and falls back to author-only.) No "For context:" opener: with all
                    // events squashed into one content, it could be read as applying to the
                    // final (live) instruction too, and we have no unambiguous delimiter.
                    // Every event boundary also gets a plain "\n" separator — 2+ events
                    // only, never before the first — so UNLABELED squashed events (e.g.
                    // adjacent model-turn JSON replies) can't run together as {...}{...}.
                    const label = (applyLabels && !suppressLabel)
                        ? `[${event.author}${event.task ? " · " + event.task : ""}] said: `
                        : "";
                    text = (pushedAnyText ? "\n" : "") + label + text;
                    firstTextOfEvent = false;
                }
                pushedAnyText = true;
                contentParts.push({ text });
            } else {
                // Push inlineData, functionCall, functionResponse, etc., completely untouched
                contentParts.push(part);
            }
        });
    });

    finalContents.push({ role, parts: contentParts });
    queue = []; // Empty the queue for the next batch
};

const forceFlatten = (modelType === "img2img") && resolvedProvider === "openai";
// Process events sequentially into the queue
for (const event of eventsToProcess) {
    // GUARD: the LIVE prompt is always the user turn eliciting this response, even when
    // prompt_author === targetAgentId (an agent prompting itself). Without this it would
    // classify as a model turn, and the request would end on role "model" — rejected by
    // Gemini. Replayed in FUTURE turns the same event correctly surfaces as a model event
    // and merges with its adjacent reply (self-talk: "I should do X" ... does X).
    const isModel = forceFlatten ? false : (event !== currentPromptEvent && event.author === targetAgentId);
    // If the role switches (and the queue isn't empty), flush the existing queue
    if (currentIsModel !== null && isModel !== currentIsModel) {
        flushQueue();
    }
    currentIsModel = isModel;
    queue.push(event);
}
flushQueue(); // Flush the final segment (this inherently handles the currentPromptEvent and any attached images)
if (finalContents.length > 0 && finalContents[0].role === "model") {
    finalContents.unshift({ role: "user", parts: [{ text: "..." }] }); // Gemini requires the first content to have role "user"
}
requestBody.contents = finalContents; // Finally, attach the constructed contents to the request payload

// === 🧾 LOG THE PROMPT AS A SESSION EVENT ===
// ADK style: the prompt is itself an immutable event in the transcript. Without it, the
// persisted log would contain only model-authored response events, and the next turn's
// QUEUE PROCESSING could never reconstruct alternating user/model Gemini history.
// We append a SANITIZED copy (image blob swapped for a tag, mirroring Node 3's replacer);
// the raw currentPromptEvent above keeps the real blob for the requestBody only.
// NOTE: .push() mutates, but that's required here - sessionEvents is a const destructure,
// and this local copy is ours to build before returning. Appended AFTER filteredEvents was
// computed, so the current prompt can't be double-included in this turn's own contents.
function sanitizeEventParts(key, value) {  // same replacer as Node 3's sanitize()
    if (typeof value === "string" && value.length > 100) {
        if (key === "data" && (this.mimeType || this.mime_type)) return "<IMAGE_BLOB(Gemini)>";
        if (key === "b64_json") return "<IMAGE_BLOB(OpenAI)>";
        if (key === "base64_img_string") return "<IMAGE_BLOB>";
        if (key === "thoughtSignature") return "<THOUGHT_SIGNATURE>";
    }
    return value;
}

sessionEvents.push({
    author: promptAuthor,
    task: currentTaskId,
    status: "ok",
    parts: JSON.parse(JSON.stringify(currentPromptEvent.parts, sanitizeEventParts)),
    timestamp: new Date().toISOString()
});

// === 📡 STATE BROADCAST (prompt event) ===
// Ship the prompt event the moment it exists, BEFORE the API call. If the model call
// or Node 3 crashes, Firestore still holds what this turn asked — the transcript ends
// AT the failure instead of one turn before it. Node 3 separately ships the response
// event when (if) the turn finishes; the listener ArrayUnions both into the Firestore
// doc's session_events_incremental (the working transcript).
// The pushed event is already sanitized (image blobs → tags), so this is text-only.
// ⚠️ LOAD-BEARING, not fire-and-forget: crash forensics and billing live in this
// stream, so a failed broadcast stops the run rather than silently losing the record.
// The generous timeout only bounds the failure case — normal turns wait one round-trip.
if (config.enable_gui_logging === true && config.gui_webhook_url) {
    try {
        await this.helpers.httpRequest({
            method: 'POST',
            url: config.gui_webhook_url,
            headers: { 'Content-Type': 'application/json' },
            body: {
                job_id: config.job_id,
                phase_id: "-",
                agent_id: targetAgentId,   // merged top-level too → live "now running" status
                task_id: currentTaskId,
                status: "running",
                timestamp: new Date().toISOString(),
                events: [sessionEvents[sessionEvents.length - 1]]
            },
            json: true,
            timeout: 10000
        });
    } catch (e) {
        // Axios strips the response body from e.message ("Request failed with status
        // code 500" is all it says) — dig the server's actual error detail out of the
        // error object, wherever this n8n build happened to nest it.
        const detail = e.response?.data ?? e.cause?.response?.data ?? e.cause?.error ?? e.description ?? null;
        const detailStr = detail ? ` — server detail: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : '';
        throw new Error(`State broadcast failed for ${targetAgentId}/${currentTaskId} (prompt event): ${e.message}${detailStr}`);
    }
}

// === 🏎️ TIER, ROUTING & NO_MODEL RESOLUTION ===
// Task tier overrides agent tier, which falls back to the type-based default.
let requestedTier = taskBlueprint.model_tier || agentBlueprint.model_tier ||
                    (isImageGen ? config.default_image_tier : config.default_text_tier);

let model_url;
let skipApi = false;
let noModelResult = null; // canned task result, only populated in no_model mode

if (requestedTier === "no_model") {
    // === ⚡ NO_MODEL MODE: skip the API, resolve the canned result locally ===
    skipApi = true;
    model_url = "no_model";

    // A task supplies its canned payload as `result` OR `textResult` (result wins if
    // both). Both are objects of named fields, templated identically and merged into
    // sessionState identically — the choice only controls how Node 3 logs the event:
    //   result     → JSON.stringify (field names carry the structure)
    //   textResult → plain text: "<name>: <value>" lines; a single field logs just
    //                its value, name omitted
    const rawResult = taskBlueprint.result !== undefined ? taskBlueprint.result : taskBlueprint.textResult;
    if (rawResult === undefined) {
        throw new Error(`CONFIGURATION ERROR: Task '${currentTaskId}' requested tier 'no_model' but defines no 'result' or 'textResult'.`);
    }
    // result/textResult is an object; we template its string leaves, so there is no JSON to escape or parse.
    noModelResult = templateObject(rawResult, sessionState);

} else {
    // === 🏎️ TIER CAPPING (honor user speed/cost ceiling from April) ===
    const tierRanks = { "fast": 1, "medium": 2, "slow": 3 };
    const maxTier = isImageGen ? (config.maximum_image_tier || "slow") : (config.maximum_text_tier || "slow");
    if (tierRanks[requestedTier] && tierRanks[maxTier] && tierRanks[requestedTier] > tierRanks[maxTier]) {
        requestedTier = maxTier;
    }

// === 🌐 DYNAMIC URL RESOLUTION (provider → type → tier) ===
    let model_name = null;  // OpenAI carries the model in the request BODY; the URL is shared across tiers
    let model_quality = null;         // OpenAI `quality`: low|medium|high|auto (config-driven, see registry)
    let model_input_fidelity = null;  // OpenAI `input_fidelity`: low|high
    try {
        const registryEntry = config.model_registry[resolvedProvider][modelType][requestedTier];
        if (!registryEntry) throw new Error("URL resolved to undefined.");
        // Registry entries: bare URL string (Gemini) OR { url, model, quality, input_fidelity }
        // (OpenAI, where every tier hits the same endpoint and the tier is expressed entirely
        // through the body params).
        model_url = typeof registryEntry === "object" ? registryEntry.url : registryEntry;
        model_name = typeof registryEntry === "object" ? (registryEntry.model || null) : null;
        model_quality = typeof registryEntry === "object" ? (registryEntry.quality || null) : null;
        model_input_fidelity = typeof registryEntry === "object" ? (registryEntry.input_fidelity || null) : null;
        if (!model_url) throw new Error("URL resolved to undefined.");
    } catch (e) {
        throw new Error(`ROUTING ERROR: Failed to resolve model URL. Provider: '${resolvedProvider}', Type: '${modelType}', Tier: '${requestedTier}'.`);
    }

    // === 🎨 OPENAI PAYLOAD OVERRIDE (img2img via /v1/images/edits) ===
    if (resolvedProvider === "openai") {
        if (!isImageGen) {
            throw new Error(`ROUTING ERROR: OpenAI provider currently supports model_type 'img2img' only (task '${currentTaskId}' asked for '${modelType}').`);
        }
        // QUEUE PROCESSING ran in forceFlatten mode, so requestBody.contents is exactly ONE
        // labeled user turn containing the scoped history + current prompt. Translate it:
        //   text parts            → joined into the single `prompt` string
        //   live inlineData parts → data-URL entries in `images` (log placeholders were
        //                           already rewritten to text markers by resolveLogPart)
        //   systemInstruction     → no OpenAI equivalent; prepended to the prompt
        const flatParts = requestBody.contents[0]?.parts || [];
        const promptPieces = [];
        const inputImages = [];
        for (const part of flatParts) {
            if (part.text !== undefined) promptPieces.push(part.text);
            const blob = part.inlineData || part.inline_data;
            if (blob?.data && !blob.data.startsWith("<IMAGE_BLOB")) {
                inputImages.push({ image_url: `data:${blob.mimeType || blob.mime_type || "image/png"};base64,${blob.data}` });
            }
        }
        const flatPrompt = [finalSystemInstruction, promptPieces.join("\n")].filter(Boolean).join("\n\n");

        requestBody = {
            ...(model_name ? { model: model_name } : {}),  // Node 3's cost lookup reads requestBody.model
            prompt: flatPrompt,
            ...(inputImages.length ? { images: inputImages } : {}),
            // Pinned per tier in the registry. Unset, OpenAI defaults to "auto" and picks the
            // output token budget itself — a ~15x per-image cost swing we'd have no control
            // over. Valid on /images/generations too, so the no-input reroute below is safe.
            ...(model_quality ? { quality: model_quality } : {}),
            size: "auto",         // scaffoldings are square today; revisit if aspect drift shows in QA
            output_format: "png"
        };
        // input_fidelity controls how finely the scaffolding is encoded (see the registry note).
        // Config-driven per tier, but only gpt-image-1.5 accepts it: gpt-image-1-mini 400s on
        // it, and gpt-image-2 rejects it too (always encodes inputs at high fidelity). It's
        // also meaningless without an input image. All three cases fall through to omitting it.
        if (inputImages.length && model_input_fidelity &&
            model_name !== "gpt-image-1-mini" && !String(model_name).startsWith("gpt-image-2")) {
            requestBody.input_fidelity = model_input_fidelity;
        }
        // /images/edits REQUIRES at least one input image. The DIRECT_IMAGE_GEN path arrives
        // with none, so reroute to the text-to-image endpoint (same body minus `images`,
        // same response shape, so Node 3 needs no extra branch).
        if (!inputImages.length) {
            model_url = model_url.replace("/images/edits", "/images/generations");
        }
    }
}

// Prepare output for the n8n HTTP Request Node
return [{
    json: {
        config,
        sessionState,
        sessionEvents,
        resolvedProvider,
        model_url,  // for API Call
        api_key: (config.api_keys && config.api_keys[resolvedProvider]),  // for API Call
        auth_mode: resolvedProvider === "openai" ? "bearer" : "query_key",  // routes the Node 2 provider switch
        requestBody,  // for API Call
        noModelResult,
        skipApi,  // for skip branch
        targetAgentId,
        currentTaskId,
        outputType,
        finalUserInstruction
    }
}];