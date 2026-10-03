// =============================================================================
// 🙋 FINAL OUTCOME  (Code node, "Run Once for All Items")
//
// The final-illustration twin of "Human outcome": the funnel for every way the final
// review can END. Two kinds of item arrive:
//   • from "Final decision" (continue | rework | fail | abort): the Resolve item, which
//     already carries human_action + human_decision_text — passed through.
//   • from "Final understood?" [true]: the Universal Agent's output after a
//     final_review_* turn. The Gatekeeper's verdict decides:
//         user_wants_to_stop      → abort
//         image_acceptable_as_is  → continue      else → rework
//
// Then: → cfg log final decision (AGENT_ID final_gate) → final_gate - log_final_decision →
// "After final log", which switches on $('Final outcome').item.json.human_action —
// THIS node's name. The Universal Agent call in between returns only
// { config, session_state, session_events } and drops top-level fields.
//
// ⚠️ Rename this node to exactly "Final outcome", and point "After final log" at it.
//    Left as a copy of "Human outcome", it would switch on the SCAFFOLD gate's result.
// =============================================================================

const GATE = {
    manager: "issue_aggregator",
    taskPrefix: "final_review",
    acceptableFlag: "image_acceptable_as_is",
    subject: "final illustration",
};

const input = items[0].json;
const { config, session_state, session_events } = input;
if (!config || !session_state || !session_events) {
    throw new Error("Final outcome — missing config / session_state / session_events on the incoming item");
}

let action = input.human_action;
let note = input.human_decision_text || "";

if (!action) {
    const round = session_events.filter(e => e.author === GATE.manager && String(e.task || "").startsWith(GATE.taskPrefix)).length;
    // Stopping first: a human who asked to stop has overridden every other reading.
    if (session_state.user_wants_to_stop === true) {
        action = "abort";
        note = `The human reviewer asked to stop the run at the ${GATE.subject} review, after ${round} exchange(s). The Final Gatekeeper read that as a request to abandon rather than to correct.`;
    } else if (session_state[GATE.acceptableFlag] === true) {
        action = "continue";
        note = `After ${round} exchange(s) the human reviewer confirmed the ${GATE.subject} is fine as painted.`;
    } else {
        action = "rework";
        note = `The human reviewer gave corrections to the ${GATE.subject} (${round} exchange(s)); the Final Gatekeeper confirmed it understands them and relayed them as fix_instructions. Retry counters reset; repainting.`;
    }
}

return [{ json: {
    config, session_state, session_events,
    human_action: action,                 // continue | rework | fail | abort
    human_decision_text: note,            // templated by log_human_decision ({human_decision_text})
    human_round: input.human_round ?? null
} }];
