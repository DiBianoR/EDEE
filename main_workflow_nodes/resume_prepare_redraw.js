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
//   3. keep_final (the Gatekeeper's choice): true = scaffold only. "Resume: scaffold only?"
//      after Archive Scaffolding then ends the job with the new base diagram and the
//      previous illustration. false = repaint too: arms the painting directives exactly as
//      "Final corrections → reset retries" does, so painting changes from the same request
//      still reach the artist after the redraw.
//   4. Makes the redo a REVISION of the approved code: finds the code that drew the
//      approved base diagram (see approvedCode below) and arms
//      retry_directive_library.plan_logic_revision_human with it for the redo's first
//      plan_logic, plus inspection_redo for consolidate_inspection. No approved code (a
//      DIRECT_IMAGE_GEN job, or an unreadable transcript) leaves both slots as they are,
//      i.e. the old fresh-pass behaviour.
//   5. Posts a human_gate "scaffold_redo" marker (with keep_final on the event, for the
//      frontend): the coding team and inspection manager see it in their history, and
//      "cfg human review" counts scaffold-review turns after it.
//
// Next: cfg amend description → image_detail_planner - amend_description → Route Workflow3,
// i.e. the normal Phase 3 from the original route onward.
// =============================================================================

const { config, session_state, session_events: events = [] } = items[0].json;
if (!config || !session_state) throw new Error("Resume: Prepare Redraw — missing config / session_state");

const changes = String(session_state.scaffold_changes || "").trim();
if (!changes) throw new Error("Resume: a redraw was requested but scaffold_changes is empty");
const keepFinal = session_state.keep_final === true;

const textOf = (ev) => (ev.parts || []).filter(p => typeof p.text === "string" && !p.thought).map(p => p.text).join("");
const jsonOf = (ev) => { try { return JSON.parse(textOf(ev)); } catch (e) { return null; } };

// 1. The description Phase 3 worked from.
let phase3Description = null;
for (const ev of events) {
    if (ev.author !== "image_detail_planner" || ev.status === "error") continue;
    const result = jsonOf(ev);
    if (result && typeof result.latest_description === "string" && result.latest_description) {
        phase3Description = result.latest_description;
    }
}

// 4a. The code that drew the approved base diagram. In the original run that is the last
//     write_code (a rejected attempt is always followed by another one). A redo round's
//     code only counts once its base diagram was accepted, shown by the artist running
//     after it (repaint too) or by "Resume: Restore Completed"'s scaffold_redo_accepted
//     marker (scaffold only). A redo that was stopped or failed ends at the next
//     resume_final_gate event without either, and its code is ignored.
let approvedCode = null;
let candidate = null;
let inRedo = false;
for (const ev of events) {
    if (ev.author === "human_gate" && ev.task === "scaffold_redo") { inRedo = true; candidate = null; continue; }
    if (ev.author === "final_gate" && ev.task === "resume_final_gate") { inRedo = false; candidate = null; continue; }
    if (inRedo && candidate &&
        (ev.author === "artist" || (ev.author === "human_gate" && ev.task === "scaffold_redo_accepted"))) {
        approvedCode = candidate; inRedo = false; candidate = null; continue;
    }
    if (ev.author !== "coder" || ev.task !== "write_code" || ev.status === "error") continue;
    const result = jsonOf(ev);
    if (!result || typeof result.python_code !== "string" || !result.python_code.trim()) continue;
    if (inRedo) candidate = result.python_code; else approvedCode = result.python_code;
}

const state = { ...session_state, keep_final: keepFinal, latest_description: phase3Description || session_state.latest_description };

// 2. Stale gate flags from the first pass.
for (const k of ["redraw_scaffold", "scaffold_acceptable_as_is", "understanding_confirmed",
                 "user_wants_to_stop", "reply_to_human", "passed_review", "passed_inspection"]) {
    delete state[k];
}

const lib = config.retry_directive_library || {};

// 3. Painting directives, as "Final corrections → reset retries" sets them (repaint only).
if (!keepFinal) {
    state.image_retry_directives = lib.plan_finishing_human || "";
    state.image_human_priority_directives = lib.final_review_human || "";
}

// 4b. Revision framing for the coding team.
if (approvedCode && lib.plan_logic_revision_human) {
    state.retry_directives = lib.plan_logic_revision_human.split("@@APPROVED_CODE@@").join(approvedCode.trim());
    state.human_priority_directives = lib.inspection_redo || "";
}

// 5. Marker.
const now = new Date().toISOString();
const marker = {
    author: "human_gate",
    task: "scaffold_redo",
    status: "ok",
    keep_final: keepFinal,
    parts: [{ text: `At the final review of this reopened job, the human asked for the base diagram itself to be redrawn, and confirmed it. Requested changes:\n${changes}\nThe diagram description is being amended to match, then the base diagram's code is revised and inspected again, and the human reviews the new base diagram. ${keepFinal
        ? "Once they accept it, the job keeps the new base diagram and the finished illustration stays exactly as it was."
        : "Once they accept it, the illustration is repainted over the new base diagram."}` }],
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
