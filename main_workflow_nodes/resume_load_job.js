// =============================================================================
// 🔁 RESUME: LOAD JOB  (Code node, "Run Once for All Items")
//
// Entry point for "Resume final review": reopens a FINISHED job at the final
// illustration gate so the human can do another correction round with the Final
// Gatekeeper. Called by the frontend through the "Resume Trigger" webhook:
//
//   POST /webhook/resume-final-gate
//   { source_job_id, mode: "overwrite" | "new", target_job_id (new mode only),
//     max_text_tier, max_image_tier, style_preference (fallback only) }
//
// This node only gathers what Load Config needs to BUILD a config. The state rebuild,
// images and job-doc setup happen in "Resume: Rebuild State", after Load Config, because
// the replay needs config.tasks (hoisted fields, textResult keys).
//
// WHY THE STYLE IS INFERRED: Load Config bakes the chosen style into the instruction
// strings and does not keep the style key, so nothing stores it. The artist's logged
// prompts (plan_finishing / render_final) quote the style description word for word, so
// matching a distinctive phrase of each description recovers it. Keep STYLE_SIGNATURES in
// step with styleLibrary in config.js; an unmatched job falls back to the frontend's
// style_preference.
//
// Refuses (throws, and the webhook answers with the error) when the source has no
// finished result to review, or when the source or target job is busy.
// =============================================================================

const STATE_MANAGER = "https://ritel-state-manager-194521282716.us-south1.run.app";
// "starting" is NOT busy: the frontend stamps it on the job just before calling this
// webhook, so its poll loop doesn't stop on the old "completed" status.
const BUSY = ["running", "awaiting_human"];
const STYLE_SIGNATURES = {
    storybook: "classic children's storybooks",
    casual_mobile: "casual mobile game art",
    cel_shaded_anime: "studio anime and cel-shaded",
    claymation_diorama: "physical diorama made of clay",
    mid_century_modern: "1950s and 1960s educational posters",
};

const body = items[0].json.body || {};
const mode = body.mode;
if (mode !== "overwrite" && mode !== "new") {
    throw new Error(`Resume: mode must be "overwrite" or "new" (got ${JSON.stringify(mode)})`);
}
const sourceId = String(body.source_job_id || "").trim();
const targetId = mode === "overwrite" ? sourceId : String(body.target_job_id || "").trim();
if (!sourceId) throw new Error("Resume: source_job_id is required");
if (!targetId) throw new Error('Resume: target_job_id is required in "new" mode');
if (mode === "new" && targetId === sourceId) throw new Error('Resume: "new" mode needs a different target_job_id');

const get = (path) => this.helpers.httpRequest({ method: "GET", url: `${STATE_MANAGER}${path}`, json: true, timeout: 30000 });

const summary = await get(`/summary/${sourceId}`);
const artifacts = summary.artifacts || {};
if (!artifacts["final_illustration.png"] || !artifacts["session_events.json"]) {
    throw new Error(`Resume: job ${sourceId} has no finished illustration to review (status: ${summary.status ?? "unknown"})`);
}
if (BUSY.includes(summary.status)) {
    throw new Error(`Resume: job ${sourceId} is ${summary.status}; wait for it to finish first`);
}
if (mode === "new") {
    // A brand-new target id normally doesn't exist yet: /summary answers 404, which is fine.
    // (Read the status directly: n8n's wrapped HTTP errors don't expose it reliably.)
    const res = await this.helpers.httpRequest({
        method: "GET", url: `${STATE_MANAGER}/summary/${targetId}`, json: true, timeout: 30000,
        returnFullResponse: true, ignoreHttpStatusErrors: true,
    });
    if (res.statusCode !== 404 && res.statusCode !== 200) {
        throw new Error(`Resume: could not check target job ${targetId} (HTTP ${res.statusCode})`);
    }
    const target = res.statusCode === 200 ? res.body || {} : {};
    if (BUSY.includes(target.status)) throw new Error(`Resume: target job ${targetId} is already ${target.status}`);
}

const transcript = (await get(`/transcript/${sourceId}`))
    .slice()
    .sort((a, b) => String(a.timestamp || "").localeCompare(String(b.timestamp || "")));

// Style: count each signature across the artist's logged prompts; the most frequent wins.
const artistPrompts = transcript
    .filter(e => e.author === "system" && ["plan_finishing", "render_final"].includes(e.task))
    .map(e => JSON.stringify(e.parts || []))
    .join(" ");
let style = null, best = 0;
for (const [key, phrase] of Object.entries(STYLE_SIGNATURES)) {
    const hits = artistPrompts.split(phrase).length - 1;
    if (hits > best) { best = hits; style = key; }
}

// Items reaching Load Config: these top-level fields are exactly what it reads to build a
// config, plus session_events (passed through). `resume` and anything else is dropped by
// Load Config, so the images never ride along; Rebuild State reaches back to this node.
return [{ json: {
    job_id: targetId,
    original_query: summary.original_query ?? null,
    style_preference: style || body.style_preference || "casual_mobile",
    max_text_tier: body.max_text_tier || "slow",
    max_image_tier: body.max_image_tier || "slow",
    human_review_mode: "wait",          // a resumed gate always waits for the human
    session_events: transcript,
    resume: {
        source_job_id: sourceId,
        target_job_id: targetId,
        mode,
        style_inferred: !!style,
        // Restored as-is if the human keeps the previous result (Resume: Restore Completed).
        previous: {
            user_message: summary.user_message ?? null,
            archival_report: summary.archival_report ?? null,
            latest_description: summary.latest_description ?? null,
            generation_successful: summary.generation_successful ?? null,
        },
    },
} }];
