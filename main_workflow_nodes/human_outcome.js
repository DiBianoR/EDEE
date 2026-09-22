// =============================================================================
// 🙋 HUMAN OUTCOME  (Code node, "Run Once for All Items")
//
// Funnel for every way the human review can END. Two kinds of item arrive:
//   • from "Human decision" (continue | rework | fail | abort): the Resolve item,
//     which already carries human_action + human_decision_text — passed through.
//   • from "Manager understood?" [true]: the Universal Agent's output after a
//     human_review_* turn. The manager's verdict decides:
//         scaffold_acceptable_as_is → continue      else → rework
//
// Everything then goes: → cfg log human decision → human_gate - log_human_decision
// → "After human log" (Switch on $('Human outcome').item.json.human_action), which
// reads THIS node's output because the Universal Agent call in between returns only
// { config, session_state, session_events } and drops top-level fields.
// =============================================================================

const input = items[0].json;
const { config, session_state, session_events } = input;
if (!config || !session_state || !session_events) {
    throw new Error("Human outcome — missing config / session_state / session_events on the incoming item");
}

let action = input.human_action;
let note = input.human_decision_text || "";

if (!action) {
    const round = session_events.filter(e => e.author === "inspection_manager" && String(e.task || "").startsWith("human_review")).length;
    if (session_state.scaffold_acceptable_as_is === true) {
        action = "continue";
        note = `After ${round} exchange(s) the human reviewer confirmed the scaffolding is fine as drawn.`;
    } else {
        action = "rework";
        note = `The human reviewer gave corrections (${round} exchange(s)); the QA Inspection Manager confirmed it understands them and relayed them as fix_instructions. Retry counters reset; redrawing the scaffolding.`;
    }
}

return [{ json: {
    config, session_state, session_events,
    human_action: action,                 // continue | rework | fail | abort
    human_decision_text: note,            // templated by log_human_decision ({human_decision_text})
    human_round: input.human_round ?? null
} }];
