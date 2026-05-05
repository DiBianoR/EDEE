// === ⚙️ CONFIGURATION ===
// Set these 3 IDs for each node instance
const PHASE_ID = items[0].json.PHASE_ID;
const AGENT_ID = items[0].json.AGENT_ID;
const TASK_ID = items[0].json.TASK_ID;

// Standard Setup
const INPUT_DATA = items[0].json;  // output of previous node
const CONFIG = INPUT_DATA.config;
const fullHistory = INPUT_DATA.history || [];

// Replaces ${VAR.PATH} placeholders with real values from the context object
// Supports: ${Node Name:variable} OR ${variable} (from localContext)
function resolveTemplate(templateStr) {
    if (typeof templateStr !== 'string') return templateStr;

    return templateStr.replace(/\$\{([^}]+)\}/g, (match, path) => {
        // 1. DEFAULT: Start with the global items[0].json
        // We can access 'items' directly because it is in the scope
        let value = items[0].json; 

        // 2. OPTIONAL: Handle Node Syntax (NodeName:variable)
        // If the path has a colon, we switch 'value' to look at that specific node
        if (path.includes(':')) {
            const [nodeName, varPath] = path.split(/:(.+)/);
            // We can access '$' directly because it is in the scope
            try {
                value = $(nodeName).first().json;
                path = varPath; // Update path to be just the variable part
            } catch (e) {
                throw new Error(`Node '${nodeName}' not found.`);
            }
        }

        // 3. Drill down into 'value'
        const keys = path.trim().split('.');
        for (const key of keys) {
            if (value === undefined || value === null) {
                 throw new Error(`MISSING: Path '${path}' not found.`);
            }
            value = value[key];
        }

        if (typeof value === 'object') return JSON.stringify(value, null, 2);
        return String(value);
    });
}

// === 🧭 NAVIGATE CONFIG ===
if (!CONFIG || !CONFIG.phases) throw new Error("Missing 'config'. Ensure Load Config runs first.");

const phaseConfig = CONFIG.phases[PHASE_ID];
if (!phaseConfig) throw new Error(`Phase ${PHASE_ID} not found in config.`);

const agentConfig = phaseConfig.agents[AGENT_ID];
if (!agentConfig) throw new Error(`Agent ${AGENT_ID} not found in Phase ${PHASE_ID}.`);

const taskConfig = agentConfig.tasks[TASK_ID];
if (!taskConfig) throw new Error(`Task ${TASK_ID} not found in Agent ${AGENT_ID}.`);

// === 🧠 ASSEMBLE IDENTITY (System Instruction) ===
const systemParts = [
  CONFIG.global_task_explanation,
  phaseConfig.identity,
  agentConfig.identity
].filter(Boolean);

const finalSystemInstruction = resolveTemplate(systemParts.join("\n\n"));

// === 📜 ASSEMBLE HISTORY ===
let scopedHistory = [];
const scope = taskConfig.history_scope || "agent";

if (scope === "global") scopedHistory = fullHistory;
else if (scope === "phase") scopedHistory = fullHistory.filter(h => h.phase_id === PHASE_ID);
else if (scope === "agent") scopedHistory = fullHistory.filter(h => h.phase_id === PHASE_ID && h.agent_id === AGENT_ID); 
// If scope is "none", scopedHistory remains []

// === 📝 CONSTRUCT USER PROMPT ===
// Priority: Task > Agent > Phase > Config Default > Error
let rawGlobalContext = taskConfig.context_override || 
                       agentConfig.context_override || 
                       phaseConfig.context_override || 
                       CONFIG.default_context_template;

if (!rawGlobalContext) {
    throw new Error("CONTEXT ERROR: No context_override found in Task/Agent/Phase and no 'default_context_template' found in Config.");
}

const globalContext = resolveTemplate(rawGlobalContext);

let historySection = scopedHistory.length > 0 ? `=== HISTORY ===\n${JSON.stringify(scopedHistory, null, 2)}` : "";

const userPrompt = `
=== CONTEXT ===
${globalContext}

${historySection}

=== YOUR CURRENT TASK ===
${resolveTemplate(taskConfig.instruction)}
`.trim();

// === 🖼️ IMAGE HANDLING ===
let parts = [{ text: userPrompt }];

if (taskConfig.view_image === true) {
    let imgData = null;
    let imgMime = null;

    // 1. Try fetching from explicit source (Configured Override)
    if (taskConfig.image_source) {
        try {
            const sourceItem = $(taskConfig.image_source).last().json;
            imgData = sourceItem.base64_img_string;
            imgMime = sourceItem.base64_img_string_mime;
        } catch (e) {
            throw new Error(`IMAGE ERROR: 'image_source' was set to '${taskConfig.image_source}', but that node execution could not be found.`);
        }
    } 
    // 2. Fallback: Try fetching from immediate input
    else {
        imgData = INPUT_DATA.base64_img_string;
        imgMime = INPUT_DATA.base64_img_string_mime;
    }

    // 3. VALIDATION: Fail if blind
    if (!imgData || !imgMime) {
        throw new Error(`CONFIGURATION ERROR: Agent '${AGENT_ID}' (Task: '${TASK_ID}') requires 'view_image: true', but no 'base64_img_string' was found in input context or specified 'image_source'.`);
    }

    // 4. Attach Image
    parts.push({
        inline_data: {
            mime_type: imgMime,
            data: imgData
        }
    });
}

// === 🎯 MODEL SELECTION ===
// Logic: Task Specific > Agent Specific > Global Default
const model_url = taskConfig.model_url || agentConfig.model_url || CONFIG.default_model_url;
const outputType = taskConfig.output_type || "json";

// Output containers
let aiResult;
let requestBody = {};
let skipApi = false;

// === ⚡ CHECK FOR NO_MODEL MODE ===
if (model_url === "no_model") {
    skipApi = true;
    const rawResult = taskConfig.result;
    if (rawResult === undefined) {
        throw new Error(`CONFIGURATION ERROR: Agent '${AGENT_ID}' (Task: '${TASK_ID}') has 'model_url':'no_model' but is missing 'result'.`);
    }

    const resolvedResult = resolveTemplate(rawResult);

    // Try to auto-parse JSON if it looks like one
    if (typeof resolvedResult === 'string' && (resolvedResult.trim().startsWith('{') || resolvedResult.trim().startsWith('['))) {
        try {
            aiResult = JSON.parse(resolvedResult);
        } catch (e) {
            aiResult = resolvedResult;
        }
    } else {
        aiResult = resolvedResult;
    }

    requestBody = { mode: "no_model", result_template: rawResult };

} else {
    // === 🚀 CONSTRUCT REQUEST (STANDARD) ===
    requestBody = {
        contents: [{ parts: parts }],
        systemInstruction: { parts: [{ text: finalSystemInstruction }] }
    };

    if (outputType === "json") {  // constrained generation - json schema
        requestBody.generationConfig = {
            response_mime_type: "application/json",
            response_schema: taskConfig.schema
        };
    } else {
        requestBody.generationConfig = {};
    }
}

return [{
    json: {
        ...INPUT_DATA,
        _model_url: model_url,
        _api_key: CONFIG.api_key,
        _request_body: requestBody,
        _output_type: outputType,
        _skip_api: skipApi,
        _no_model_result: aiResult,
        _task_instruction: taskConfig.instruction,
        _phase_id: PHASE_ID,
        _agent_id: AGENT_ID,
        _task_id: TASK_ID,
        _full_history: fullHistory
    }
}];