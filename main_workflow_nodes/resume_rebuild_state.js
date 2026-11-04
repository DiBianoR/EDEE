// =============================================================================
// 🔁 RESUME: REBUILD STATE  (Code node, "Run Once for All Items")
//
// Runs after "Load Resume Config", which built a fresh config (inferred style, current
// tiers, human_review_mode "wait") and seeded session_state with original_query and
// the empty directive slots. This node:
//
//   1. Rebuilds session_state by replaying the transcript: every agent turn whose text
//      is a JSON object is merged in order, exactly as Universal Agent node 3 did live,
//      minus the task's hoist_result_fields. Single-field textResult (no_model) turns
//      log only their value, so the key comes from config. Prompts, errors and images
//      add nothing. Variables that were only ever swept in from the top level (retry
//      counters, directives) are not recoverable, and the final-gate path does not need
//      them: Load Config seeded the directive slots and "Final corrections" re-arms them.
//   2. Fetches the scaffold and final illustration from the public job bucket.
//   3. Overwrite mode: asks the state manager to copy the job's current result into
//      {job_id}/previous/ first, and refuses to continue if it can't.
//   4. Sets up the target job doc through /update-state: status running, a
//      final_gate "resume_final_gate" marker event, the final illustration as latest.png
//      (what the gate shows). "new" mode also copies the transcript and the scaffold.
//   5. Hands Final Gate: Announce a fresh gate (stage gate, reason review).
//
// The scaffold and final image ride on THIS node's output (resume_* fields). Announce
// drops them; cfg25 / cfg26 and Resume: Restore Completed reach back here by name.
// ⚠️ Keep the name "Resume: Rebuild State": those three nodes reference it.
// =============================================================================

const BUCKET = "https://storage.googleapis.com/edee-job-archives-0925957935";
const load = $("Resume: Load Job").first().json;
const { source_job_id: sourceId, target_job_id: targetId, mode, style_source } = load.resume;
const { config, session_state: seeded, session_events: events } = items[0].json;
if (!config || !seeded || !Array.isArray(events)) {
    throw new Error("Resume: Rebuild State — missing config / session_state / session_events from Load Resume Config");
}
const stateManager = config.gui_webhook_url.replace(/\/update-state$/, "");

// === 1. Replay ===
const state = { ...seeded };
let applied = 0;
for (const ev of events) {
    // run_settings records launch choices for Load Job, not an agent's output.
    if (["system", "user", "run_settings"].includes(ev.author) || ev.status === "error") continue;
    const task = config.tasks[ev.task] || {};
    const text = (ev.parts || []).filter(p => typeof p.text === "string" && !p.thought).map(p => p.text).join("");
    if (!text) continue;
    let result = null;
    try { result = JSON.parse(text); } catch (e) { /* plain-text turn */ }
    if (result && typeof result === "object" && !Array.isArray(result)) {
        Object.assign(state, result);
    } else if (task.textResult && Object.keys(task.textResult).length === 1) {
        state[Object.keys(task.textResult)[0]] = text;
    } else {
        continue;
    }
    for (const k of task.hoist_result_fields || []) delete state[k];
    applied++;
}
if (!state.latest_description) {
    throw new Error(`Resume: could not rebuild latest_description from job ${sourceId}'s transcript (${applied} turns replayed)`);
}

// === 2. Images ===
const fetchImage = async (name) => {
    const res = await this.helpers.httpRequest({
        method: "GET", url: `${BUCKET}/${sourceId}/${name}`,
        encoding: "arraybuffer", returnFullResponse: true, timeout: 60000,
    });
    return { b64: Buffer.from(res.body).toString("base64"), mime: (res.headers["content-type"] || "image/png").split(";")[0] };
};
const finalImage = await fetchImage("final_illustration.png");
// DIRECT_IMAGE_GEN jobs have the stock placeholder in scaffolding.png: nothing to paint over.
const noScaffold = state.selected_workflow === "DIRECT_IMAGE_GEN";
const scaffold = noScaffold ? { b64: "", mime: "" } : await fetchImage("scaffolding.png");

// === 3. Back up the result we are about to overwrite ===
if (mode === "overwrite") {
    try {
        await this.helpers.httpRequest({ method: "POST", url: `${stateManager}/backup-round/${targetId}`, json: true, timeout: 60000 });
    } catch (e) {
        throw new Error(`Resume: could not back up job ${targetId} to previous/ (${e.message}); refusing to overwrite it. Is the state manager up to date?`);
    }
}

// === 4. Target job doc ===
const now = new Date();
const marker = {
    author: "final_gate",
    task: "resume_final_gate",
    status: "ok",
    parts: [{ text: mode === "overwrite"
        ? `The finished job was reopened at the final illustration review on ${now.toISOString().slice(0, 10)} for another correction round. The illustration under review is the one this job finished with.`
        : `This job continues job ${sourceId}, reopened at the final illustration review on ${now.toISOString().slice(0, 10)} for another correction round. The illustration under review is the one that job finished with.` }],
    timestamp: now.toISOString(),
    model: "none",
    cost: 0,
};
const sessionEvents = [...events, marker];

await this.helpers.httpRequest({
    method: "POST",
    url: config.gui_webhook_url,
    headers: { "Content-Type": "application/json" },
    json: true,
    timeout: 60000,
    body: {
        job_id: targetId,
        status: "running",
        timestamp: now.toISOString(),
        phase_id: "5",
        agent_id: "final_gate",
        task_id: "resume_final_gate",
        original_query: state.original_query ?? null,
        // "new": the whole history moves to the new job. "overwrite": the doc already has it.
        events: mode === "new" ? sessionEvents : [marker],
        base64_img_string: finalImage.b64,               // → {job_id}/latest.png + live_image
        base64_img_string_mime: finalImage.mime,
        ...(mode === "new"
            ? (noScaffold ? { scaffolding_placeholder: true }
                          : { scaffolding_base64_img_string: scaffold.b64, scaffolding_base64_img_string_mime: scaffold.mime })
            : {}),
        ...(mode === "new" ? { resumed_from: sourceId } : {}),
    },
});

// === 5. Into the final gate ===
return [{ json: {
    config,
    session_state: state,
    session_events: sessionEvents,
    human_stage: "gate",
    human_reason: "review",
    resume_scaffold_b64: scaffold.b64,
    resume_scaffold_mime: scaffold.mime,
    resume_final_b64: finalImage.b64,
    resume_final_mime: finalImage.mime,
    resume_note: `${applied} turns replayed; style ${load.style_preference} (${style_source})`,
} }];
