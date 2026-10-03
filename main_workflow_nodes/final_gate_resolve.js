// =============================================================================
// 🙋 FINAL GATE: RESOLVE  (Code node, "Run Once for All Items")
//
// The final-illustration twin of "Human Gate: Resolve". Runs right after "Final Gate:
// Wait" and turns the two ways a Wait node can resume — a webhook call, or its time
// limit — into ONE item for the "Final decision" Switch.
//
// Output: human_action ∈ continue | message | extend | rework | fail | abort, plus
// human_message, human_decision_text and the echoed stage / reason / round.
//
//   gate         continue            → continue   (release as painted; max_retries: accept anyway)
//   gate/conv    message             → message
//   gate review  timeout             → continue   (the "30 s then automatic" mode, or absent human)
//   gate max_r.  timeout             → fail       (nobody rescued it — same outcome as no human)
//   conv         continue / timeout  → rework if the Gatekeeper already holds corrections,
//                                      else continue / fail exactly as the gate would
//   any          lapsed but active   → extend     (see STILL THERE?)
//
// ⚠️ Every name and flag is in GATE. An unedited copy of the scaffold version would
//    read "Human Gate: Announce" and scaffold_acceptable_as_is — the SCAFFOLD gate's
//    data — and nothing would fail loudly.
// =============================================================================

const GATE = {
    announceNode: "Final Gate: Announce",   // must match the canvas name exactly
    acceptableFlag: "image_acceptable_as_is",
    subject: "final illustration",
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

const announced = latestRun(GATE.announceNode);
if (!announced) throw new Error(`Final Gate: Resolve — no output from '${GATE.announceNode}' found`);
const { config, session_state, session_events, human_stage: stage, human_reason: reason,
        human_round: round, human_wait_seconds: waitSeconds, human_opened_at: openedAt } = announced;

const incoming = items[0].json || {};
const body = incoming.body && typeof incoming.body === "object" ? incoming.body : null;
let action = body && body.action ? String(body.action).toLowerCase() : "timeout";
const message = body && body.message ? String(body.message).trim() : "";
if (action === "corrections" && message) action = "message";
const timedOut = action === "timeout";

// === ⏱️ STILL THERE? ===
// The slice lapsed. If /human-activity has pushed the deadline into the future, the
// human is mid-thought: open another slice rather than deciding for them.
let extendSeconds = 0;
let lastActivity = null;
if (timedOut && (config.human_review_mode || "automatic") !== "automatic") {
    try {
        const summary = await this.helpers.httpRequest({
            method: "GET",
            url: `${config.gui_webhook_url.replace(/\/update-state$/, "")}/summary/${config.job_id}`,
            json: true,
            timeout: 10000
        });
        const gate = summary?.human_review || {};
        lastActivity = gate.last_activity ?? null;
        const remaining = (new Date(gate.deadline).getTime() - Date.now()) / 1000;
        if (gate.last_activity && Number.isFinite(remaining) && remaining > 5) {
            extendSeconds = remaining;
        }
    } catch (e) {
        console.error("Activity check failed, treating the wait as expired:", e.message);
    }
}

if (extendSeconds) {
    return [{ json: {
        config, session_state, session_events,
        human_action: "extend",
        human_stage: stage,
        human_reason: reason,
        human_round: round,
        human_opened_at: openedAt,
        human_last_activity: lastActivity,
        human_extend_seconds: extendSeconds
    } }];
}

const heldCorrections = round > 0 && session_state[GATE.acceptableFlag] !== true;
const minutes = Math.round((waitSeconds || 0) / 60);

let outcome, note = "";
switch (action) {
    case "message":
    case "corrections":
        outcome = action;                       // no history note: the conversation IS the record
        break;
    case "abort":
        outcome = "abort";
        note = `The human reviewer chose to stop the run at the ${GATE.subject} review.`;
        break;
    default: {                                  // continue | timeout
        if (stage === "conversation" && heldCorrections) {
            outcome = "rework";
            note = timedOut
                ? `The human reviewer gave corrections to the ${GATE.subject} but stopped answering (${minutes} min); proceeding with the corrections gathered so far. Retry counters reset.`
                : `The human reviewer gave corrections to the ${GATE.subject} and asked us to proceed. The Final Gatekeeper's fix_instructions carry them. Retry counters reset.`;
        } else if (reason === "max_retries" && timedOut) {
            outcome = "fail";
            note = `The ${GATE.subject} failed review repeatedly and the human reviewer did not respond within ${minutes} min.`;
        } else if (reason === "max_retries") {
            outcome = "continue";
            note = `The ${GATE.subject} failed review repeatedly; the human reviewer chose to accept it as it is and release it.`;
        } else if (timedOut) {
            outcome = "continue";
            note = `The human reviewer was offered a review of the approved ${GATE.subject} and did not respond within ${waitSeconds}s; releasing it automatically.`;
        } else {
            outcome = "continue";
            note = `The human reviewer looked at the approved ${GATE.subject} and accepted it as painted.`;
        }
    }
}

return [{ json: {
    config, session_state, session_events,
    human_action: outcome,
    human_message: message,
    human_stage: outcome === "corrections" ? "conversation" : stage,
    human_reason: reason,
    human_round: round,
    human_opened_at: outcome === "corrections" ? openedAt : null,
    human_decision_text: note
} }];
