// =============================================================================
// 🔁 RESUME: AFTER SPOT EDIT  (Code node, "Run Once for All Items")
//
// Back to the final gate after "image_editor - edit_final". On success the Universal
// Agent has already broadcast the edited image to {job_id}/latest.png, so the gate shows
// it. There is no Phase 5 review for a spot edit: the human looking at it IS the review.
// If no image came back, a final_gate note says so (the Gatekeeper sees it in history)
// and the gate reopens on the unchanged illustration.
// =============================================================================

const out = items[0].json;
const { config, session_state, session_events: events = [] } = out;
let sessionEvents = events;

if (out.agent_error || !out.base64_img_string) {
    const now = new Date().toISOString();
    const note = {
        author: "final_gate",
        task: "spot_edit",
        status: "ok",
        parts: [{ text: "The spot edit failed: the image editor returned no image. The illustration is unchanged." }],
        timestamp: now,
        model: "none",
        cost: 0,
    };
    sessionEvents = [...events, note];
    if (config.enable_gui_logging === true && config.gui_webhook_url) {
        await this.helpers.httpRequest({
            method: "POST",
            url: config.gui_webhook_url,
            headers: { "Content-Type": "application/json" },
            json: true,
            timeout: 15000,
            body: { job_id: config.job_id, status: "running", timestamp: now, phase_id: "5",
                    agent_id: "final_gate", task_id: "spot_edit", events: [note] },
        });
    }
}

// The image stays off this item: Announce rebuilds the item without it anyway.
return [{ json: {
    config,
    session_state,
    session_events: sessionEvents,
    human_stage: "gate",
    human_reason: "review",
} }];
