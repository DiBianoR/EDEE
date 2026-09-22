// =============================================================================
// 🙋 HUMAN GATE: ANNOUNCE  (Code node, "Run Once for All Items")
//
// Publishes the scaffolding-review gate to the job doc (status "awaiting_human" +
// a `human_review` block the frontend renders) and hands the following Wait node
// its time limit. No image rides along: the human is looking at {job_id}/latest.png,
// which "Prepare for Vision" already broadcast when it rendered this very scaffold.
// ⚠️ That broadcast is therefore LOAD-BEARING for the review — see the note on its
//    timeout in main_workflow_nodes/README.md §5.
// Sits in front of "Human Gate: Wait"; three edges feed it:
//   • "Human review?" / "Human available?"  — fresh gate after the machine verdict
//   • "Human decision" [corrections]         — the human clicked Give corrections
//                                              without typing anything yet
//   • "Manager understood?" [false]          — the manager asked a question; wait
//                                              for the human's answer
//
// STAGE   gate          the human may continue / give corrections / give up
//         conversation  a message round with the QA Inspection Manager
// REASON  review        the inspectors PASSED the scaffold (normal gate)
//         max_retries   the inspectors gave up on it; the human is the rescue
// WAIT    gate+review in "timeout" mode → config.human_review_timeouts.gate_seconds
//         everything else                → absent_minutes × 60 ("human walked away")
//
// The listener must be told about the gate, or waiting is pointless — but a dead
// listener must not hang the run for ten minutes either, so on a failed POST the
// wait collapses to one second and "Human Gate: Resolve" sees an ordinary timeout.
//
// ⚠️ $execution.resumeUrl is the ONE-SHOT resume URL of the Wait node that follows.
//    The frontend never calls it directly: ritel-state-manager /human-decision does.
// ⚠️ NEVER reach back by name with a bare .first()/.last()/.all() — see the
//    latestRun() note in "Archive Scaffolding". Same helper, same reason.
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

const input = items[0].json;
const { config, session_state, session_events } = input;
if (!config || !session_state || !session_events) {
    throw new Error("Human Gate: Announce — missing config / session_state / session_events on the incoming item");
}

const mode = config.human_review_mode || "automatic";
const timeouts = config.human_review_timeouts || { gate_seconds: 30, absent_minutes: 10 };

// Rounds so far = manager replies in the human_review conversation (no counter state).
const round = session_events.filter(e => e.author === "inspection_manager" && String(e.task || "").startsWith("human_review")).length;

// Stage: explicit from "Human Gate: Resolve" (corrections click), else inferred —
// straight after a human_review reply we are mid-conversation; otherwise it's a gate.
const lastEvent = session_events[session_events.length - 1] || {};
const midConversation = lastEvent.author === "inspection_manager" && String(lastEvent.task || "").startsWith("human_review");
const stage = input.human_stage || (midConversation ? "conversation" : "gate");

// Reason: on a fresh gate the machine verdict decides; inside a conversation keep
// whatever the gate was opened with.
const reason = input.human_reason
    || (stage === "gate" ? (session_state.passed_inspection === true ? "review" : "max_retries")
                         : ((latestRun("Human Gate: Announce") || {}).human_reason || "review"));

const graceSeconds = (Number(timeouts.absent_minutes) || 10) * 60;

// SLICE, not the whole budget. A Wait node's time limit is fixed when it starts, so a
// running wait can never be extended. Instead each wait covers one slice; when it
// lapses, "Human Gate: Resolve" checks the deadline that /human-activity keeps pushing
// out and sends us round again if the human is demonstrably still there. So the opening
// 30s of "timeout" mode is 30s OF SILENCE, and every other wait is "N minutes since the
// last sign of life" rather than N minutes flat.
let waitSeconds = (stage === "gate" && reason === "review" && mode === "timeout")
    ? Number(timeouts.gate_seconds) || 30
    : graceSeconds;

// Re-announce after activity: serve out what is left of the extended deadline instead
// of starting a fresh full slice.
if (input.human_extend_seconds) {
    waitSeconds = Math.max(15, Math.min(graceSeconds, Math.round(Number(input.human_extend_seconds))));
}

const now = new Date();
// opened_at survives extensions: the frontend keys its gate widgets on it, so a moving
// deadline must NOT change it — re-keying would discard whatever the human has typed
// so far, at precisely the moment they are typing it.
const openedAt = input.human_opened_at || now.toISOString();
const humanReview = {
    stage,
    reason,
    mode,
    resume_url: $execution.resumeUrl,          // fresh every slice; the listener reads it live
    deadline: new Date(now.getTime() + waitSeconds * 1000).toISOString(),
    wait_seconds: waitSeconds,
    active_grace_seconds: graceSeconds,        // /human-activity extends to now + this
    last_activity: input.human_last_activity ?? null,
    round,
    last_reply: session_state.reply_to_human ?? null,
    understanding_confirmed: session_state.understanding_confirmed === true,
    opened_at: openedAt
};

if (config.enable_gui_logging === true && config.gui_webhook_url) {
    try {
        await this.helpers.httpRequest({
            method: "POST",
            url: config.gui_webhook_url,
            headers: { "Content-Type": "application/json" },
            body: {
                job_id: config.job_id,
                status: "awaiting_human",
                timestamp: now.toISOString(),
                phase_id: "3",
                agent_id: "inspection_manager",
                task_id: stage === "gate" ? "human_review_gate" : "human_review_conversation",
                human_review: humanReview
                // NO image: "Prepare for Vision" already broadcast this very render, so
                // {job_id}/latest.png is current and the frontend is already showing it.
                // Re-sending the base64 would just be a second full-size copy per gate.
            },
            json: true,
            timeout: 15000
        });
    } catch (e) {
        console.error("Human gate could not be announced — skipping the wait:", e.message);
        waitSeconds = 1;
    }
}

return [{ json: {
    config, session_state, session_events,
    human_stage: stage,
    human_reason: reason,
    human_round: round,
    human_wait_seconds: waitSeconds,   // ← "Human Gate: Wait" reads this: {{ $json.human_wait_seconds }}
    human_deadline: humanReview.deadline,
    human_opened_at: openedAt          // carried through extensions, for the widget keys
} }];
