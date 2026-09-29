// =============================================================================
// 📦 CONTROL FINAL BROADCAST — terminal payload for the control-group workflow.
// Stripped-down "Send Final Broadcast" from the Main Workflow: same listener, same
// field names, so /summary, the Test Orchestrator's Fetch Job Summary, and the
// frontend read a control run exactly like a pipeline run.
//
// Input: the Universal Agent's output for control_render — { config, session_state,
// session_events } plus base64_img_string at top level when an image came back.
//
// No image → status "failed" (a clean terminal status: the listener still
// materializes session_events.json), so the sheet row says Failed, not Completed.
// ⚠️ NEVER include `config` (contains api_keys) in the payload.
// =============================================================================

const {
    config = {},
    session_state: ss = {},
    session_events: events = [],
    base64_img_string: img = null,
    base64_img_string_mime: mime = "image/png"
} = items[0].json;

const totalCost = Number(events.reduce((sum, ev) => sum + (ev.cost || 0), 0).toFixed(6));
const ok = !!img;
const userMessage = ok
    ? "Control run: single-call illustration generated from the raw query."
    : "Control run: the image model returned no image.";

const finalPayload = {
    job_id: config.job_id,
    status: ok ? "completed" : "failed",
    timestamp: new Date().toISOString(),
    phase_id: "-",
    agent_id: "-",
    task_id: "-",
    total_cost: totalCost,
    base64_img_string: img,
    base64_img_string_mime: mime,
    session_events: events,
    user_message: userMessage,
    ...(ok ? {} : { error_message: userMessage }),
    archival_report: null,
    generation_successful: ok,
    latest_description: null,
    original_query: ss.original_query ?? null
};

if (config.enable_gui_logging === true && config.gui_webhook_url) {
    try {
        await this.helpers.httpRequest({
            method: 'POST',
            url: config.gui_webhook_url,
            headers: { 'Content-Type': 'application/json' },
            body: finalPayload,
            json: true,
            timeout: 30000
        });
    } catch (e) {
        throw new Error(`Final broadcast failed — completion NOT persisted: ${e.message}`);
    }
}

return [{ json: {
    status: "Workflow Complete",
    job_id: config.job_id,
    generation_successful: ok,
    user_message: userMessage,
    total_cost: totalCost,
    session_events: events
} }];
