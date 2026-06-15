// === ⚙️ CONFIGURATION ===
const inputData = items[0].json;
const {
    config,
    session_state: sessionState = {},  // state: contains a flat dictionary of misc. variables
    session_events: sessionEvents = [],  // history: contains list of {author, task, parts[text, inlineData, fileData, functionCall, functionResponse], actions[], partial, timestamp} objs
    prompt_author: promptAuthor = "system",  // used if another agent directly wrote the prompt
    TASK_ID: currentTaskId,
    AGENT_ID: providedAgentId,
    base64_img_string,  // If we have an input image
    base64_img_string_mime,
    ...externalVars
} = inputData;
Object.assign(sessionState, externalVars);  // Other variables into sessionState. If same name exists, overwritten.
if (!config) throw new Error("CONFIGURATION ERROR: No 'config' object found in the payload.");
if (!config.tasks || !config.agents) throw new Error("CONFIGURATION ERROR: The provided 'config' is missing 'tasks' or 'agents' registries.");

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
const rawHistoryScope = agentBlueprint.history_scope || [];  // A list of agent IDs
const historyScope = Array.isArray(rawHistoryScope) ? rawHistoryScope : [rawHistoryScope];  // Allow single item as string
const filteredEvents = sessionEvents.filter(event => historyScope.includes(event.author));

// === 🖼️️️ FINISH CONSTRUCTING CURRENT PROMPT ===
const currentParts = [{ text: finalUserInstruction }];
if (base64_img_string && base64_img_string_mime) {
    currentParts.unshift({ // unshift adds the element to the front of the array
        inlineData: {
            mimeType: base64_img_string_mime,
            data: base64_img_string
        }
    });
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
    let labeledAnyText = false;
    queue.forEach((event) => {
        let labeledFirstTextOfEvent = false;
        event.parts.forEach((part) => {
            if (part.text !== undefined) {
                let text = part.text;
                // Apply the label to the FIRST text block of the event, if required
                if (applyLabels && !labeledFirstTextOfEvent) {
                    const prefix = labeledAnyText === false ? `${event.author} said: ` : `\n${event.author} said: `;
                    text = prefix + text;
                    labeledAnyText = true;
                    labeledFirstTextOfEvent = true;
                }
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

// Process events sequentially into the queue
for (const event of eventsToProcess) {
    const isModel = event.author === targetAgentId;
    // If the role switches (and the queue isn't empty), flush the existing queue
    if (currentIsModel !== null && isModel !== currentIsModel) {
        flushQueue();
    }
    currentIsModel = isModel;
    queue.push(event);
}
flushQueue(); // Flush the final segment (this inherently handles the currentPromptEvent and any attached images)
if (finalContents.length > 0 && finalContents[0].role === "model") {
    finalContents.unshift({ role: "user", parts: [{ text: "" }] }); // Gemini requires the first content to have role "user"
}
requestBody.contents = finalContents; // Finally, attach the constructed contents to the request payload

//[WIP]
// === 🏎️ TIER, ROUTING & NO_MODEL RESOLUTION ===
// Task tier overrides agent tier, which falls back to the type-based default.
let requestedTier = taskBlueprint.model_tier || agentBlueprint.model_tier ||
                    (isImageGen ? config.default_image_tier : config.default_text_tier);

let model_url;
let skipApi = false;
let aiResult; // only populated in no_model mode

if (requestedTier === "no_model") {
    // === ⚡ NO_MODEL MODE: skip the API, resolve the canned result locally ===
    skipApi = true;
    model_url = "no_model";

    const rawResult = taskBlueprint.result;
    if (rawResult === undefined) {
        throw new Error(`CONFIGURATION ERROR: Task '${currentTaskId}' requested tier 'no_model' but defines no 'result'.`);
    }
    // result is an object; we template its string leaves, so there is no JSON to escape or parse.
    noModelResult = templateObject(rawResult, sessionState);

} else {
    // === 🏎️ TIER CAPPING (honor user speed/cost ceiling from April) ===
    const tierRanks = { "fast": 1, "medium": 2, "slow": 3 };
    const maxTier = isImageGen ? (config.maximum_image_tier || "slow") : (config.maximum_text_tier || "slow");
    if (tierRanks[requestedTier] && tierRanks[maxTier] && tierRanks[requestedTier] > tierRanks[maxTier]) {
        requestedTier = maxTier;
    }

    // === 🌐 DYNAMIC URL RESOLUTION (provider → type → tier) ===
    const provider = config.active_provider || "google";
    try {
        model_url = config.model_registry[provider][modelType][requestedTier];
        if (!model_url) throw new Error("URL resolved to undefined.");
    } catch (e) {
        throw new Error(`ROUTING ERROR: Failed to resolve model URL. Provider: '${provider}', Type: '${modelType}', Tier: '${requestedTier}'.`);
    }
}

// Prepare output for the n8n HTTP Request Node
return [{
    json: {
        _model_url: model_url,
        _api_key: config.api_key,
        _request_body: requestBody,
        _skip_api: skipApi
    }
}];