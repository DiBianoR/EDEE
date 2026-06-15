// === 1. Extract Data ===
// The HTTP node usually overwrites the payload, so we pull the original config/state from Node 1
// WARNING: Ensure '1. State Compiler' matches the EXACT name of your Node 1 in n8n.
const originalInput = $('1. State Compiler').item.json;
const geminiResponse = items[0].json;

const sessionState = originalInput.session_state || {};
const sessionEvents = originalInput.session_events || [];
const agentName = originalInput._agent_name || "Agent";
const outputType = originalInput._output_type || "text";

// === 2. Parse the Output ===
let rawText = "";
try {
    rawText = geminiResponse.candidates[0].content.parts[0].text;
} catch (e) {
    throw new Error("Failed to extract text from Gemini response.");
}

let parsedResult = rawText;

// Auto-parse if the agent was supposed to return JSON
if (outputType === "json") {
    try {
        // Strip markdown blocks if the model wrapped it despite the generationConfig
        const cleanJson = rawText.replace(/```json\n?|```/g, '').trim();
        parsedResult = JSON.parse(cleanJson);
    } catch (e) {
        parsedResult = rawText;
    }
}

// === 3. Update Session State (Mutable Working Memory) ===
// If the LLM returned a JSON Object, we use Object.assign to merge those new keys into the state.
// Example: If parsedResult is {"planning_text": "xyz"}, sessionState.planning_text now equals "xyz".
// (This is why your Task schemas in config.js should always have "type: OBJECT" at the root level).
if (typeof parsedResult === 'object' && !Array.isArray(parsedResult)) {
    Object.assign(sessionState, parsedResult);
} else {
    // If it's just raw text (or a naked array), save it securely under a generic draft key.
    sessionState.current_draft = parsedResult;
}

// === 4. Update Session Events (Immutable Transcript) ===
// We append the standard "[Agent] said:" format to maintain the ADK-style trace
sessionEvents.push({
    role: "user",
    parts: [{ text: `[${agentName}] said: ${typeof parsedResult === 'string' ? parsedResult : JSON.stringify(parsedResult)}` }]
});

// === 5. GUI Webhook Broadcast (Fire & Forget) ===
if (originalInput.config && originalInput.config.enable_gui_logging && originalInput.config.gui_webhook_url) {
    const firestorePayload = {
        job_id: originalInput.config.job_id,
        agent_id: agentName,
        task_id: originalInput.TASK_ID,
        response: parsedResult,
        status: "completed",
        timestamp: new Date().toISOString()
    };
    // Implement your n8n webhook HTTP request here if needed.
}

// === 6. Output the updated Session Object ===
// This returns the cleanly updated state back out to your Main Workflow,
// ready for the next n8n Set Node to assign a new TASK_ID and loop again.
return [{
    json: {
        config: originalInput.config,
        session_state: sessionState,
        session_events: sessionEvents,
        latest_output: parsedResult
    }
}];