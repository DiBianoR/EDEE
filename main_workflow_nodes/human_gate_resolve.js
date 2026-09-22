// =============================================================================
// 🙋 HUMAN GATE: RESOLVE  (Code node, "Run Once for All Items")
//
// Runs right after "Human Gate: Wait" and normalizes the two ways a Wait node can
// resume into ONE item for the "Human decision" Switch:
//   • resumed by webhook  → items[0].json is the request ({headers, params, query,
//                           body}); body = what ritel-state-manager /human-decision
//                           forwarded: { action, message, job_id, decided_at, ... }
//   • time limit reached  → items[0].json is our own Announce item passed through
//                           (no `body`) — the human did not answer
//
// Output (top-level, for the Switch and the cfg nodes downstream):
//   human_action   continue | message | corrections | rework | fail | abort
//   human_message  the human's text (message rounds)
//   human_stage / human_reason / human_round  — echoed from Announce
//   human_decision_text — the note log_human_decision writes into history
//
// DECISION TABLE (stage / reason / what came back → action)
//   gate         continue            → continue   (accept as drawn; on max_retries: accept anyway)
//   gate         corrections (empty) → corrections (open the conversation, wait for text)
//   gate/conv    corrections + text  → message
//   conv         message             → message
//   any          abort               → abort
//   gate review  timeout             → continue   (the "30 s then automatic" mode, or absent human)
//   gate max_r.  timeout             → fail       (nobody rescued it — same outcome as no human)
//   conv         continue / timeout  → rework if the manager already holds corrections
//                                      (round ≥ 1 and scaffold not marked acceptable),
//                                      else continue / fail exactly as the gate would
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

const announced = latestRun("Human Gate: Announce");
if (!announced) throw new Error("Human Gate: Resolve — no output from 'Human Gate: Announce' found");
const { config, session_state, session_events, human_stage: stage, human_reason: reason,
        human_round: round, human_wait_seconds: waitSeconds, human_opened_at: openedAt } = announced;

const incoming = items[0].json || {};
const body = incoming.body && typeof incoming.body === "object" ? incoming.body : null;
let action = body && body.action ? String(body.action).toLowerCase() : "timeout";
const message = body && body.message ? String(body.message).trim() : "";
if (action === "corrections" && message) action = "message";
const timedOut = action === "timeout";

// === ⏱️ STILL THERE? ===
// The slice lapsed. /human-activity has been pushing the gate's deadline out on every
// keystroke, so a deadline still in the future means the human is mid-thought: open
// another slice rather than deciding for them. Only asked on the timeout branch, and
// only when a human could plausibly be present.
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
        // Unreachable listener: fall through and time out normally. Waiting longer on
        // the strength of a lookup we could not perform would be the wrong default.
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

const heldCorrections = round > 0 && session_state.scaffold_acceptable_as_is !== true;
const minutes = Math.round((waitSeconds || 0) / 60);
const failedTimes = (session_state.inspection_retry_count || (announced.session_state || {}).inspection_retry_count || 3);

let outcome, note = "";
switch (action) {
    case "message":
    case "corrections":
        outcome = action;                       // no history note: the conversation IS the record
        break;
    case "abort":
        outcome = "abort";
        note = "The human reviewer chose to stop the run at the scaffolding review.";
        break;
    default: {                                  // continue | timeout
        if (stage === "conversation" && heldCorrections) {
            outcome = "rework";
            note = timedOut
                ? `The human reviewer gave corrections but stopped answering (${minutes} min); proceeding with the corrections gathered so far. Retry counters reset.`
                : `The human reviewer gave corrections and asked us to proceed. The QA Inspection Manager's fix_instructions carry them. Retry counters reset.`;
        } else if (reason === "max_retries" && timedOut) {
            outcome = "fail";
            note = `The scaffolding failed inspection repeatedly and the human reviewer did not respond within ${minutes} min.`;
        } else if (reason === "max_retries") {
            outcome = "continue";
            note = "The scaffolding failed inspection repeatedly; the human reviewer chose to accept it as it is and continue.";
        } else if (timedOut) {
            outcome = "continue";
            note = `The human reviewer was offered a review of the approved scaffolding and did not respond within ${waitSeconds}s; proceeding automatically.`;
        } else {
            outcome = "continue";
            note = "The human reviewer looked at the approved scaffolding and accepted it as drawn.";
        }
    }
}

return [{ json: {
    config, session_state, session_events,
    human_action: outcome,
    human_message: message,
    human_stage: outcome === "corrections" ? "conversation" : stage,   // corrections click → Announce opens the conversation
    human_reason: reason,
    human_round: round,
    // A corrections click reopens the SAME gate as a conversation, so the frontend's
    // widgets should survive the transition; a decision that ends the gate does not care.
    human_opened_at: outcome === "corrections" ? openedAt : null,
    human_decision_text: note
} }];
