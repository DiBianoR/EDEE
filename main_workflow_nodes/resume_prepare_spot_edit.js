// =============================================================================
// 🔁 RESUME: PREPARE SPOT EDIT  (Code node, "Run Once for All Items")
//
// Resumed jobs only. Reached from "Resume: spot edit?" when the Final Gatekeeper's resume
// variant set spot_edit: one or two small, local changes to the finished illustration,
// made by the image_editor agent (task edit_final) instead of a repaint over the scaffold.
// Also acts as the cfg for that call (PHASE / AGENT / TASK + the image to edit).
//
// WHICH IMAGE: the current one, i.e. whichever of "artist render_final" and
// "image_editor edit_final" succeeded LAST in the transcript. If that turn happened in the
// job's history rather than in this execution, its node has no output here, and the image
// the job was resumed with (Resume: Rebuild State) is that same image.
//
// DRIFT: every spot edit regenerates the image a little. The marker posted here says how
// many have been applied in a row since the last full repaint; the Gatekeeper reads it and
// reminds the human once two are in a row (rule in final_review_open_resume).
// =============================================================================

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

const { config, session_state, session_events: events = [] } = items[0].json;
const instructions = String(session_state.edit_instructions || "").trim();
if (!instructions) throw new Error("Resume: a spot edit was requested but edit_instructions is empty");

const isRender = e => e.status === "ok" && e.author === "artist" && e.task === "render_final";
const isEdit = e => e.status === "ok" && e.author === "image_editor" && e.task === "edit_final";

let last = null, inARow = 0;
for (const e of events) {
    if (isRender(e)) { last = "artist"; inARow = 0; }
    else if (isEdit(e)) { last = "editor"; inARow++; }
}
const fromNode = last === "editor" ? latestRun("image_editor - edit_final")
               : last === "artist" ? latestRun("artist - render_final") : null;
const rebuilt = $("Resume: Rebuild State").first().json;
const image = fromNode && fromNode.base64_img_string
    ? { b64: fromNode.base64_img_string, mime: fromNode.base64_img_string_mime || "image/png" }
    : { b64: rebuilt.resume_final_b64, mime: rebuilt.resume_final_mime || "image/png" };
if (!image.b64) throw new Error("Resume: no current illustration found to edit");

const n = inARow + 1;
const now = new Date().toISOString();
const marker = {
    author: "final_gate",
    task: "spot_edit",
    status: "ok",
    parts: [{ text: `Spot edit ${n} in a row since the last full repaint (spot edits regenerate the image slightly each time, so drift accumulates). Requested change:\n${instructions}` }],
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
        body: { job_id: config.job_id, status: "running", timestamp: now, phase_id: "4",
                agent_id: "final_gate", task_id: "spot_edit", events: [marker] },
    });
}

const state = { ...session_state };
delete state.spot_edit;
delete state.redraw_scaffold;

return [{ json: {
    config,
    session_state: state,
    session_events: [...events, marker],
    PHASE_ID: "4",
    AGENT_ID: "image_editor",
    TASK_ID: "edit_final",
    base64_img_string: image.b64,
    base64_img_string_mime: image.mime,
} }];
