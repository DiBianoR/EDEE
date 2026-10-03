// =============================================================================
// 🙋 FINAL GATE: ANNOUNCE  (Code node, "Run Once for All Items")
//
// The final-illustration twin of "Human Gate: Announce". Publishes the review gate to
// the job doc (status "awaiting_human" + a `human_review` block the frontend renders)
// and hands "Final Gate: Wait" its time limit. Three edges feed it:
//   • "Final review?" / "Final available?"  — fresh gate after the Gatekeeper's verdict
//   • "Final decision" [corrections]         — kept for parity; the one-button UI never
//                                              sends it, so this edge never fires
//   • "Final understood?" [false]            — the Gatekeeper asked a question; wait
//                                              for the human's answer
//
// No image rides along: node 3 of "artist - render_final" already broadcast this very
// illustration, so {job_id}/latest.png is current and the frontend is showing it.
//
// ⚠️ EVERY name and flag this gate depends on is in GATE below. The scaffold version
//    hard-codes "Human Gate: Announce", "inspection_manager" and "human_review" — a
//    pasted copy left unedited would reach back into the SCAFFOLD gate and read its data
//    without any error to warn you. Node names here must match your canvas exactly.
// ⚠️ NEVER reach back by name with a bare .first()/.last()/.all() — see the
//    latestRun() note in "Archive Scaffolding". Same helper, same reason.
// =============================================================================

const GATE = {
    gate: "final",                          // tells the frontend which wording to show
    announceNode: "Final Gate: Announce",   // THIS node's own name, for the reach-back
    manager: "issue_aggregator",            // who holds the conversation
    taskPrefix: "final_review",             // final_review_open / final_review_reply
    passedFlag: "final_pass",               // the Gatekeeper's verdict: review vs max_retries
    phase: "5",
};

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
    throw new Error(`${GATE.announceNode} — missing config / session_state / session_events on the incoming item`);
}

const mode = config.human_review_mode || "automatic";
const timeouts = config.human_review_timeouts || { gate_seconds: 30, absent_minutes: 10 };

const isGateTurn = e => e.author === GATE.manager && String(e.task || "").startsWith(GATE.taskPrefix);

// Rounds so far = this gate's manager replies (no counter state).
const round = session_events.filter(isGateTurn).length;

// Stage: explicit from "Final Gate: Resolve", else inferred — straight after one of
// this gate's replies we are mid-conversation; otherwise it's a fresh gate.
const lastEvent = session_events[session_events.length - 1] || {};
const midConversation = isGateTurn(lastEvent);
const stage = input.human_stage || (midConversation ? "conversation" : "gate");

// Reason: on a fresh gate the verdict decides; inside a conversation keep whatever the
// gate was opened with.
const reason = input.human_reason
    || (stage === "gate" ? (session_state[GATE.passedFlag] === true ? "review" : "max_retries")
                         : ((latestRun(GATE.announceNode) || {}).human_reason || "review"));

const graceSeconds = (Number(timeouts.absent_minutes) || 10) * 60;

// SLICE, not the whole budget — see "Human Gate: Announce". The opening window in
// "timeout" mode is gate_seconds OF SILENCE; typing extends it via /human-activity.
let waitSeconds = (stage === "gate" && reason === "review" && mode === "timeout")
    ? Number(timeouts.gate_seconds) || 30
    : graceSeconds;

if (input.human_extend_seconds) {
    waitSeconds = Math.max(15, Math.min(graceSeconds, Math.round(Number(input.human_extend_seconds))));
}

const now = new Date();
// opened_at survives extensions: the frontend keys its gate widgets on it.
const openedAt = input.human_opened_at || now.toISOString();
const humanReview = {
    gate: GATE.gate,
    stage,
    reason,
    mode,
    resume_url: $execution.resumeUrl,          // fresh every slice; the listener reads it live
    deadline: new Date(now.getTime() + waitSeconds * 1000).toISOString(),
    wait_seconds: waitSeconds,
    active_grace_seconds: graceSeconds,        // /human-activity extends to now + this
    last_activity: input.human_last_activity ?? null,
    round,
    // Only mid-conversation. reply_to_human is shared state, and on a fresh gate it
    // still holds the SCAFFOLD manager's last words.
    last_reply: midConversation ? (session_state.reply_to_human ?? null) : null,
    understanding_confirmed: midConversation && session_state.understanding_confirmed === true,
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
                phase_id: GATE.phase,
                agent_id: GATE.manager,
                task_id: stage === "gate" ? `${GATE.taskPrefix}_gate` : `${GATE.taskPrefix}_conversation`,
                human_review: humanReview
            },
            json: true,
            timeout: 15000
        });
    } catch (e) {
        console.error(`${GATE.announceNode}: gate could not be announced — skipping the wait:`, e.message);
        waitSeconds = 1;
    }
}

return [{ json: {
    config, session_state, session_events,
    human_stage: stage,
    human_reason: reason,
    human_round: round,
    human_wait_seconds: waitSeconds,   // ← "Final Gate: Wait" reads this: {{ $json.human_wait_seconds }}
    human_deadline: humanReview.deadline,
    human_opened_at: openedAt
} }];
