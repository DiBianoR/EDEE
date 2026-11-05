// =============================================================================
// 🔁 RESUME: PREPARE REDRAW  (Code node, "Run Once for All Items")
//
// Resumed jobs only. Reached from "Resume: redraw scaffold?" when the Final Gatekeeper's
// resume variant (final_review_*_resume) set redraw_scaffold: the human confirmed a change
// to the base diagram itself. Sets the run up to go back through Phase 3:
//
//   1. Restores the description Phase 3 originally worked from. Phase 4's product_designer
//      rewrites latest_description (adding invented brands/themes), and Phase 3 was built
//      never to see that. The Phase 3 version is simply the last latest_description written
//      by image_detail_planner (merge_plans / review_description, or an earlier
//      amend_description), so it's recovered from the transcript and works on any job.
//      Falls back to the current value if the transcript has none.
//   2. Clears gate flags left over from the first pass, so the redo's scaffold gate and the
//      next final gate decide from fresh answers.
//   3. Arms the painting directives exactly as "Final corrections → reset retries" does, so
//      painting changes from the same request still reach the artist after the redraw.
//   4. Posts a human_gate "scaffold_redo" marker: the coding team and inspection manager see
//      it in their history, and "cfg human review" counts scaffold-review turns after it.
//
// Next: cfg amend description → image_detail_planner - amend_description → Route Workflow3,
// i.e. the normal Phase 3 from the original route onward.
// =============================================================================

const { config, session_state, session_events: events = [] } = items[0].json;
if (!config || !session_state) throw new Error("Resume: Prepare Redraw — missing config / session_state");

const changes = String(session_state.scaffold_changes || "").trim();
if (!changes) throw new Error("Resume: a redraw was requested but scaffold_changes is empty");

// 1. The description Phase 3 worked from.
let phase3Description = null;
for (const ev of events) {
    if (ev.author !== "image_detail_planner" || ev.status === "error") continue;
    const text = (ev.parts || []).filter(p => typeof p.text === "string" && !p.thought).map(p => p.text).join("");
    try {
        const result = JSON.parse(text);
        if (result && typeof result.latest_description === "string" && result.latest_description) {
            phase3Description = result.latest_description;
        }
    } catch (e) { /* not a JSON turn */ }
}

const state = { ...session_state, latest_description: phase3Description || session_state.latest_description };

// 2. Stale gate flags from the first pass.
for (const k of ["redraw_scaffold", "scaffold_acceptable_as_is", "understanding_confirmed",
                 "user_wants_to_stop", "reply_to_human", "passed_review", "passed_inspection"]) {
    delete state[k];
}

// 3. Painting directives, as "Final corrections → reset retries" sets them.
const lib = config.retry_directive_library || {};
state.image_retry_directives = lib.plan_finishing_human || "";
state.image_human_priority_directives = lib.final_review_human || "";

// 4. Marker.
const now = new Date().toISOString();
const marker = {
    author: "human_gate",
    task: "scaffold_redo",
    status: "ok",
    parts: [{ text: `At the final review of this reopened job, the human asked for the base diagram itself to be redrawn, and confirmed it. Requested changes:\n${changes}\nThe diagram description is being amended to match, then the base diagram is planned, coded and inspected again.` }],
    timestamp: now,
    model: "none",
    cost: 0,
};

if (config.enable_gui_logging === true && config.gui_webhook_url) {
    await this.helpers.httpRequest({
        method: "POST",
        url: config.gui_webhook_url,
        headers: { "Content-Type": "application/json" },
        json: true,
        timeout: 15000,
        body: { job_id: config.job_id, status: "running", timestamp: now, phase_id: "3",
                agent_id: "human_gate", task_id: "scaffold_redo", events: [marker] },
    });
}

return [{ json: { config, session_state: state, session_events: [...events, marker] } }];
