// =============================================================================
// 🔁 RESUME: RESTORE COMPLETED  (Code node, "Run Once for All Items")
//
// Ends a resumed job WITHOUT re-running Phase 6, when the human's resumed round changed
// nothing that needs a new report. Reached from two IFs after "After final log":
//   • continue, and the artist never ran in this execution: approved as is, walked away
//     (the gate timed out), or the Gatekeeper concluded no changes were wanted. The
//     report from the original run still describes this exact image, so re-generating it
//     would only spend tokens.
//   • abort: the human asked to stop the round. Stopping a resume means keeping what
//     the job had, even if a repaint already happened in this round.
//
// Marks the job completed with the illustration it was resumed with, its previous
// user message and archival report, and the full transcript including this round. The
// state manager writes final_illustration.png (deleting latest.png) and re-materializes
// session_events.json + total_cost.json, as for any completed run.
// =============================================================================

// ⚠️ .item does not exist in "Run Once for All Items" mode, and Final outcome runs once
//    per gate round, so take its LAST run (same helper as Archive Scaffolding).
const latestRun = (nodeName) => {
    let latest = null;
    for (let run = 0; run < 50; run++) {
        let data;
        try { data = $(nodeName).all(0, run); }
        catch (e) { break; }
        if (data && data.length) latest = data[data.length - 1].json;
    }
    return latest;
};

const rebuilt = $("Resume: Rebuild State").first().json;
const load = $("Resume: Load Job").first().json;
const { config, session_state, session_events: events = [] } = items[0].json;
const action = (latestRun("Final outcome") || {}).human_action;
const previous = load.resume.previous || {};
const now = new Date().toISOString();

const note = {
    author: "final_gate",
    task: "resume_final_gate",
    status: "ok",
    parts: [{ text: action === "abort"
        ? "The human reviewer stopped the resumed review round. The job keeps the illustration and report it had before the round."
        : "The resumed review round ended without a repaint. The job keeps the illustration and report it had before the round." }],
    timestamp: now,
    model: "none",
    cost: 0,
};
// Same run_settings record Send Final Broadcast writes, so a resume of THIS job finds
// the style without inferring it (see "Resume: Load Job").
const settings = {
    author: "run_settings",
    task: "run_settings",
    status: "ok",
    parts: [{ text: JSON.stringify({
        style_preference: config.style_preference ?? null,
        max_text_tier: config.maximum_text_tier ?? null,
        max_image_tier: config.maximum_image_tier ?? null,
    }) }],
    timestamp: now,
    model: "none",
    cost: 0,
};
const transcript = [...events, note, settings];
const totalCost = Number(transcript.reduce((sum, ev) => sum + (ev.cost || 0), 0).toFixed(6));

// ⚠️ NEVER include `config` (contains api_keys): the listener merges this payload into
//    the job doc.
await this.helpers.httpRequest({
    method: "POST",
    url: config.gui_webhook_url,
    headers: { "Content-Type": "application/json" },
    json: true,
    timeout: 30000,
    body: {
        job_id: config.job_id,
        status: "completed",
        timestamp: now,
        phase_id: "-",
        agent_id: "-",
        task_id: "-",
        total_cost: totalCost,
        base64_img_string: rebuilt.resume_final_b64,
        base64_img_string_mime: rebuilt.resume_final_mime || "image/png",
        events: [note, settings],
        session_events: transcript,
        user_message: previous.user_message,
        archival_report: previous.archival_report,
        generation_successful: previous.generation_successful ?? true,
        latest_description: previous.latest_description ?? session_state.latest_description ?? null,
        original_query: session_state.original_query ?? null,
        human_review: { gate: "final", stage: "closed", last_action: action === "abort" ? "abort" : "kept_previous" },
    },
});

return [{ json: {
    status: "Resume closed: previous result kept",
    job_id: config.job_id,
    human_action: action,
    total_cost: totalCost,
} }];
