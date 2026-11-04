"""
Regenerate the EDEE-main entries of n8n_node_notes.json from the live mirror.

    py -3 main_workflow_nodes/build_node_notes.py   # then: py -3 n8n_notes.py check / apply

Each note = "<SECTION> · <what the node is>. <hand-written context> | in: … | out: …".
The wiring part (in/out, branch names) is generated from "Main Workflow.json", so it is
always true to the graph the mirror was taken from; the section and context come from
the tables below. Run n8n_backup.py first so the mirror is current.

Keep notes to road-sign length. The long explanations live in STRUCTURE.md.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKFLOW_ID = "HtwI6u1ae2xwKw1f"

# --- sections: every node belongs to exactly one -------------------------------------
SECTIONS = {
    "ENTRY": ["When clicking ‘Execute workflow’", "Select Query", "Mock Input1", "API Trigger",
              "When Executed by Another Workflow", "Set Job", "Set API Key", "Load Phase 1 Config"],
    "P1 VALIDATION": ["cfg60", "problem_validation - extract_math_and_visual", "problem_validation - Logic Router",
                      "Problem Validation Agent - Switch", "cfg3", "problem_validation - check_conflict", "valid",
                      "cfg41", "problem_validation - format_ready_branch", "cfg40", "error_handler - report_unknown_error3",
                      "cfg4", "image_description - propose_diagram", "cfg42", "problem_validation - format_math_only_branch",
                      "cfg43", "problem_validation - format_visual_only_branch", "cfg5", "error_handler - report_unknown_error1",
                      "Problem Validation Agent - Output"],
    "P2 SPECIALISTS": ["Load Phase 2 Config", "cfg6", "image_detail_planner - review_request", "cfg7",
                       "dimension_expert - estimate_dimensions", "cfg8", "layout_expert - plan_composition", "cfg9",
                       "visual_director - plan_viewpoint", "cfg10", "markup_specialist - plan_markings", "cfg11",
                       "educator - enhance_clarity"],
    "P2 SITUATIONAL": ["cfg32", "image_detail_planner - situational_planning", "If 3D Objects", "cfg12", "3d_specialist - plan_3d",
                       "If Graphs", "cfg13", "data_viz_expert - plan_graph", "If Arranged Objects", "cfg14",
                       "arrangement_planner - plan_object_arrangement", "If Artistic Planning", "cfg33",
                       "artistic_planner - artistic_planning"],
    "P2 MERGE+REVIEW LOOP": ["cfg15", "image_detail_planner - merge_plans", "cfg16", "image_detail_planner - review_description",
                             "If Ready for code", "retry_count", "cfg44", "error_handler - report_error3", "Phase 2 - Output"],
    "P3 ROUTE+DESIGN": ["Load Phase 3 Config", "cfg17", "selector - choose_path", "Route Workflow3", "cfg inject constraints",
                        "scaffolding_manager - inject_constraints", "cfg34", "scaffolding_designer - design_scaffolding"],
    "P3 CODING LOOP": ["cfg18", "coder - plan_logic", "cfg19", "coder - write_code", "cfg20", "reviewer - syntax_check", "cfg21",
                       "reviewer - logic_check", "cfg52", "reviewer - math_check", "cfg64", "review_manager - consolidate_review",
                       "if bool_var3", "cfg56", "review_manager - troubleshoot", "retry_count3", "cfg54", "error_handler - report_error4"],
    "P3 EXECUTION": ["Hoist python_code", "Python Execution Node", "cfg57", "review_manager - log_error", "if bool_var4", "cfg59",
                     "review_manager - troubleshoot1", "retry_count4", "cfg58", "error_handler - report_error5"],
    "P3 VISION QA": ["Prepare for Vision", "cfg22", "inspector - verify_adherence", "cfg37", "inspector - verify_perspective",
                     "cfg23", "inspector - check_overlaps", "cfg24", "inspector - detect_artifacts", "cfg63",
                     "inspection_manager - consolidate_inspection", "inspections passed?", "cfg55",
                     "inspection_manager - troubleshoot_visual", "retry_count2", "cfg53", "error_handler - report_error2"],
    "P3 SCAFFOLD GATE (human)": ["Human review?", "Human available?", "Human Gate: Announce", "Human Gate: Wait", "Human Gate: Resolve",
                                 "Human decision", "cfg human review", "inspection_manager - human_review", "Manager understood?",
                                 "Human outcome", "cfg log human decision", "human_gate - log_human_decision", "After human log",
                                 "Human corrections → reset retries", "cfg human abort", "error_handler - report_error7"],
    "P3 EXIT": ["Archive Scaffolding", "Phase 3 - Output"],
    "P4 PRODUCTS": ["Load Phase 4 Config", "If customizable_products_in_phase4", "cfg66", "product_scout - detect_customizable_products",
                    "If Highly Customizable Products", "cfg65", "product_designer - customizable_product_planning"],
    "P4 ARTIST": ["cfg25", "artist - plan_finishing", "cfg26", "artist - render_final", "Render OK?", "cfg render abort",
                  "error_handler - report_error9", "Phase 4 - Output"],
    "P5 REVIEW LOOP": ["Load Phase 5 Config", "cfg27", "image_verifier - review_bias", "cfg28", "image_verifier - review_aesthetics",
                       "cfg29", "image_verifier - review_safety", "cfg30", "image_verifier3 - review_math", "cfg31",
                       "issue_aggregator - aggregate_feedback", "if final_pass", "cfg62", "issue_aggregator - troubleshoot_visual",
                       "retry_count5", "cfg61", "error_handler - report_error6"],
    "P5 FINAL GATE (human)": ["Final review?", "Final available?", "Final Gate: Announce", "Final Gate: Wait", "Final Gate: Resolve",
                              "Final decision", "cfg final review", "issue_aggregator - final_review", "Final understood?",
                              "Final outcome", "cfg log final decision", "final_gate - log_final_decision", "After final log",
                              "Final corrections → reset retries", "cfg final abort", "error_handler - report_error8"],
    "P5 EXIT": ["Phase 5 - Output"],
    "P5 RESUME (finished job → final gate)": ["Resume Trigger", "Resume: Load Job", "Resume: Respond OK", "Set API Key (resume)",
                                             "Load Resume Config", "Resume: Rebuild State", "Resume: keep previous? (continue)",
                                             "Resume: keep previous? (abort)", "Resume: Restore Completed"],
    "P6 REPORT": ["Load Phase 6 Config", "cfg38", "final_reporter - generate_user_message", "cfg39",
                  "final_reporter - generate_archival_report", "Phase 6 - Output", "Send Final Broadcast"],
    # Detached: reusable building blocks, copy-pasted to build new circuits. Never run.
    "TEMPLATE (detached) basic agent call": ["cfg", "universal agent"],
    "TEMPLATE (detached) agent-driven IF": ["cfg49", "T/F agent", "if bool_var1"],
    "TEMPLATE (detached) agent IF with retry loop": ["cfg45", "T/F RETRY agent", "if bool_var", "cfg35", "error_expert - troubleshoot",
                                                     "retry_count1", "cfg46", "error_handler - report_error1"],
    "TEMPLATE (detached) agent-driven SWITCH": ["cfg50", "CASE(enum) agent", "Route Workflow (enum_var)"],
    "TEMPLATE (detached) agent picks next task": ["cfg51", "CASE(enum) agent1", "task picker", "chosen task"],
    "TEMPLATE (detached) enum→task lookup": ["task picker1", "chosen task1"],
    "TEMPLATE (detached) code-computed next task": ["complex conditional", "chosen task2"],
    "TEMPLATE (detached) error exit": ["cfg47", "error_handler - report_error"],
    "TEMPLATE (detached) unknown-error exit": ["cfg48", "error_handler - report_unknown_error"],
    "LEGACY (detached, unused)": ["cfg1", "problem_validation - extract_math", "cfg2", "problem_validation - extract_visual",
                                 "cfg36", "inspector - detect_error_loops", "If in error loop", "Mock Input", "TODO: Primitives"],
    "DEBUG TOOL (detached)": ["View Image"],
}

# --- hand-written context, keyed by node name ----------------------------------------
CONTEXT = {
    # entry
    "When clicking ‘Execute workflow’": "Manual test run from the editor.",
    "Select Query": "Pick query_index (1-based) from Mock Input1's library, plus style.",
    "Mock Input1": "Query library + payload builder for manual runs; skips Set Job.",
    "API Trigger": "Frontend's generate-diagram webhook (/webhook = published, /webhook-test = draft).",
    "When Executed by Another Workflow": "Test Orchestrator calls EDEE-main here; no human_review_mode sent, so it runs automatic.",
    "Set Job": "Normalizes the launch payload: job_id, original_query, style, tiers, human_review_mode (default automatic).",
    "Set API Key": "Holds the Gemini/OpenAI keys (redacted in git by the pre-commit hook).",
    "Load Phase 1 Config": "FIRST config build: bakes style into instructions and seeds session_state. Later Load Phase N Config calls pass config/state through.",
    # P1
    "cfg60": "One call extracts both the math problem and the visual request.",
    "problem_validation - Logic Router": "Sets validation_route from math_found / image_request_found.",
    "Problem Validation Agent - Switch": "Output names differ from values: both=ready, problem=math_only.",
    "cfg3": "Both found: check they don't conflict.",
    "valid": "Conflict check verdict (session_state.valid).",
    "cfg4": "Math only: invent the most educational diagram.",
    "cfg43": "Visual only: format the request as-is.",
    "cfg5": "Neither found: clean failure.",
    "cfg40": "Math and visual conflict: clean failure.",
    "Problem Validation Agent - Output": "Phase 1 exit; strips the item to config/session_state/session_events.",
    # P2
    "Load Phase 2 Config": "Pass-through config reload (phase boundary).",
    "cfg6": "Detail planner (manager) reviews the request before fanning out.",
    "cfg32": "Planner sets needs_3d / needs_graph / needs_arrangement / requires_artistic flags for the IF chain below.",
    "If 3D Objects": "Each IF either runs its specialist or skips to the next IF.",
    "If Artistic Planning": "False skips straight to merge (cfg15).",
    "cfg15": "Merge every specialist plan into one rendering-ready description.",
    "cfg16": "Increments retry_count for the description-review loop.",
    "If Ready for code": "Branches on session_state.ready_for_artist (review_description's verdict); MaxRetries/False also count retry_count at $('cfg16').",
    "retry_count": "Loop-back: re-reads retry_count from cfg16 and returns to it.",
    "cfg44": "Description failed review 3×: MaxRetriesExceeded.",
    "Phase 2 - Output": "Phase 2 exit; strips the item to the envelope.",
    # P3 route/design
    "Load Phase 3 Config": "Pass-through config reload (phase boundary).",
    "cfg17": "Selector picks STANDARD_DIAGRAM / COMPOSITE_PRIMITIVES / DIRECT_IMAGE_GEN.",
    "Route Workflow3": "COMPOSITE_PRIMITIVES shares the STANDARD path (see TODO: Primitives). DIRECT_IMAGE_GEN skips scaffolding entirely.",
    "cfg inject constraints": "Code cfg: composes situational_directives from directive_library entries whose planner flag is true.",
    "scaffolding_manager - inject_constraints": "no_model task: assembles the stage-3 briefing.",
    "cfg34": "Designs the geometric blueprint the coder implements.",
    # P3 coding loop
    "cfg18": "LOOP HEAD for all Phase 3 redo paths: increments coding_retry_count and carries retry_directives. 5 entries.",
    "cfg19": "Write the matplotlib script (python_code is hoisted, not kept in state).",
    "cfg20": "Three reviewers in sequence: syntax → logic → math.",
    "cfg64": "Review manager merges the three reviews into passed_review + fix_instructions.",
    "if bool_var3": "Branches on session_state.passed_review; MaxRetries/False count coding_retry_count at $('cfg18').",
    "cfg56": "Code rejected: review manager turns reviews into fix instructions.",
    "retry_count3": "Loop-back to cfg18 carrying the three retry counters.",
    "cfg54": "Code rejected 3×: MaxRetriesExceeded.",
    # P3 execution
    "Hoist python_code": "Lifts python_code from coder - write_code; increments execution_retry_count.",
    "Python Execution Node": "POST to render-matplotlib (Cloud Run). Error output [1] carries the traceback.",
    "cfg57": "Code cfg: turns the HTTP error into a readable PYTHON RUNTIME ERROR for the review manager.",
    "if bool_var4": "Execution failed 3×? → fail, else troubleshoot and recode.",
    "retry_count4": "Loop-back to cfg18; resets coding_retry_count to 0.",
    "cfg58": "Execution failed 3×: MaxRetriesExceeded.",
    # P3 vision QA
    "Prepare for Vision": "Binary PNG → base64, and broadcasts it to {job_id}/latest.png. That broadcast is what the scaffold gate shows the human.",
    "cfg22": "Four inspectors in sequence, each attaches the render: adherence → perspective → overlaps → artifacts.",
    "cfg63": "Inspection manager verdict; increments inspection_retry_count.",
    "inspections passed?": "True → scaffold gate. MaxRetries → rescue gate (human) or fail. False → troubleshoot_visual.",
    "cfg55": "Rejected render: inspection manager writes visual fix instructions.",
    "retry_count2": "Loop-back to cfg18; resets coding and execution counters.",
    "cfg53": "Shared failure exit: scaffold rejected 3× with no human, or human gate 'fail'.",
    # scaffold gate
    "Human review?": "Mode ≠ automatic → open the scaffold gate, else skip to Archive Scaffolding.",
    "Human available?": "MaxRetries rescue: ask the human instead of failing (non-automatic modes).",
    "Human Gate: Announce": "Publishes status awaiting_human + resume_url; picks the wait slice. Source: main_workflow_nodes/human_gate_announce.js.",
    "Human Gate: Wait": "Resumes on webhook POST from the state manager, or when the slice times out.",
    "Human Gate: Resolve": "Turns the resume (or timeout) into human_action. Source: human_gate_resolve.js.",
    "Human decision": "Output 'extend' (also matches legacy 'corrections') → Announce opens another wait slice while the human is typing. The UI itself only sends message / continue.",
    "cfg human review": "Human's text → inspection_manager (prompt_author user). TASK_ID picks _open vs _reply.",
    "Manager understood?": "False → ask the human again (conversation stage).",
    "Human outcome": "Funnel: reads user_wants_to_stop / scaffold_acceptable_as_is → abort / continue / rework. Source: human_outcome.js.",
    "cfg log human decision": "Logs the decision as a human_gate event (no_model).",
    "After human log": "Routes on $('Human outcome').human_action.",
    "Human corrections → reset retries": "Zeroes all three counters and arms [HUMAN IN THE LOOP] directives, then back to cfg18.",
    "cfg human abort": "Human asked to stop: UserAborted.",
    # P3 exit
    "Archive Scaffolding": "Pins the approved scaffold (or the placeholder on DIRECT_IMAGE_GEN) to {job_id}/scaffolding.png. 3 entries.",
    "Phase 3 - Output": "Phase 3 exit; strips the item to the envelope.",
    # P4
    "Load Phase 4 Config": "Pass-through config reload (phase boundary).",
    "If customizable_products_in_phase4": "Config switch: run product_scout in Phase 4?",
    "cfg25": "LOOP HEAD of the artist: increments image_gen_retry_count; attaches the scaffold from $('Prepare for Vision'), or $('Resume: Rebuild State') in a resumed job. 4 entries.",
    "cfg26": "Render; MODEL_TYPE txt2img on DIRECT_IMAGE_GEN, otherwise img2img over the scaffold ($('Prepare for Vision'), or $('Resume: Rebuild State') when resumed).",
    "Render OK?": "No image (agent_error) → retry render up to 3×, then cfg render abort.",
    "cfg render abort": "Image model returned no image 3×: RenderFailed.",
    "Phase 4 - Output": "Phase 4 exit; strips the item, so later nodes reach the image via $('artist - render_final').",
    # P5
    "Load Phase 5 Config": "Pass-through config reload (phase boundary).",
    "cfg27": "Four verifiers in sequence, each attaches the render from $('artist - render_final'): bias → aesthetics → safety → math.",
    "cfg31": "Final Gatekeeper merges the reviews into final_pass.",
    "if final_pass": "True → final gate. MaxRetries (counted at $('cfg25')) → rescue gate or fail. False → troubleshoot.",
    "cfg62": "Rejected illustration: Gatekeeper writes repaint instructions.",
    "retry_count5": "Loop-back to cfg25 with its retry count.",
    "cfg61": "Shared failure exit: illustration rejected 3× with no human, or final gate 'fail'.",
    # final gate
    "Final review?": "Mode ≠ automatic → open the final gate, else skip to Phase 5 - Output.",
    "Final available?": "MaxRetries rescue: ask the human instead of failing.",
    "Final Gate: Announce": "Final-gate twin of Human Gate: Announce. Source: final_gate_announce.js.",
    "Final Gate: Wait": "Shares the execution's resume URL with the scaffold gate's Wait.",
    "Final Gate: Resolve": "Source: final_gate_resolve.js.",
    "Final decision": "Output 'extend' (also matches legacy 'corrections') → Announce opens another wait slice while the human is typing.",
    "cfg final review": "Human's text → issue_aggregator. TASK_ID counts final_review turns since the last resume marker (not scaffold ones).",
    "Final outcome": "Reads user_wants_to_stop / image_acceptable_as_is. Source: final_outcome.js.",
    "cfg log final decision": "Logs as final_gate (not human_gate), so the two gates' histories stay apart.",
    "After final log": "Routes on $('Final outcome').human_action. continue/abort pass through the Resume: keep previous? checks.",
    "Final corrections → reset retries": "Zeroes image_gen_retry_count and arms the human repaint directives; geometry is fixed at this stage.",
    "cfg final abort": "Human asked to stop: UserAborted.",
    "Phase 5 - Output": "Phase 5 exit; strips the item to the envelope.",
    # resume
    "Resume Trigger": "Frontend's 'Resume final review' webhook (resume-final-gate); answers via Resume: Respond OK. Source: main_workflow_nodes/resume_*.js.",
    "Resume: Load Job": "Checks the source job is finished and not busy, loads its transcript, infers the style from the artist's prompts. Source: resume_load_job.js.",
    "Resume: Respond OK": "Answers the frontend once the job is accepted; a throw before this answers with the error.",
    "Set API Key (resume)": "Copy of Set API Key for the resume entry.",
    "Load Resume Config": "Builds a fresh config (inferred style, current tiers, human_review_mode wait); passes the transcript through.",
    "Resume: Rebuild State": "Replays the transcript into session_state, backs up (overwrite) or copies (new) the job, posts the resume marker, opens the final gate. cfg25/cfg26/Restore reach back here BY NAME. Source: resume_rebuild_state.js.",
    "Resume: keep previous? (continue)": "Resumed and the artist never ran → keep the previous result, no Phase 6. Otherwise the normal Phase 5 exit.",
    "Resume: keep previous? (abort)": "Resumed → a stop keeps the previous result; otherwise the normal abort.",
    "Resume: Restore Completed": "Marks the job completed with its previous image, message and report (no model calls). Source: resume_restore_completed.js.",
    # P6
    "Load Phase 6 Config": "Pass-through config reload (phase boundary).",
    "Send Final Broadcast": "Completion payload to the state manager: final image from $('artist - render_final'), user message, transcript. A failure here stops the run.",
    # templates
    "cfg": "Fill PHASE/AGENT/TASK.",
    "if bool_var1": "Branches on session_state.bool_var, which the agent's schema must output.",
    "cfg45": "Increments retry_count.",
    "if bool_var": "True output left open for the circuit you paste it into.",
    "cfg35": "False: troubleshoot, then loop back.",
    "retry_count1": "Loop-back to cfg45.",
    "cfg46": "MaxRetriesExceeded exit.",
    "Route Workflow (enum_var)": "Branches on session_state.enum_var (VAL1/VAL2/VAL3 placeholders).",
    "task picker": "Maps enum_var → TASK_ID via taskMap (placeholder entries).",
    "task picker1": "Same map without the agent; enum_var comes from upstream.",
    "complex conditional": "Code decides TASK_ID from several state conditions (example cases).",
    # legacy / debug
    "cfg1": "Old two-call Phase 1, replaced by extract_math_and_visual (cfg60).",
    "cfg36": "Error-loop detector, never wired in; its IF has an empty condition.",
    "If in error loop": "Unfinished: empty condition, no outputs.",
    "Mock Input": "Old fixed mock payload, replaced by Select Query + Mock Input1.",
    "TODO: Primitives": "Placeholder for a COMPOSITE_PRIMITIVES path; that route currently uses the STANDARD path.",
    "View Image": "Debug: paste after any node to view its base64_img_string as a binary image.",
}

UA = "EDEE — Universal Agent Sub-Workflow"


def describe(node, ins):
    t = node["type"].split(".")[-1]
    p = node["parameters"]
    if t == "set":
        a = {x["name"]: x["value"] for x in p.get("assignments", {}).get("assignments", [])}
        if "AGENT_ID" in a:
            agent, task = a.get("AGENT_ID") or "?", a.get("TASK_ID") or "?"
            if str(task).startswith("="):
                task = "(computed)"
            return f"cfg for {agent}.{task}" if agent != "?" else "cfg (blank: fill PHASE/AGENT/TASK)"
        if not a:
            return "Set (raw JSON)"
        if set(a) <= {"config", "session_state", "=session_state", "session_events"}:
            return "Phase output (Set)"
        return "Set: " + ", ".join(k.lstrip("=") for k in a)
    if t == "executeWorkflow":
        wf = (p.get("workflowId") or {}).get("cachedResultName") or ""
        if "Load Config" in wf:
            return "Load Config sub-workflow"
        cfg = ins[0][0] if ins else None
        return "Universal Agent call" + (f" (set up by {cfg})" if cfg else "")
    return {"if": "IF", "switch": "Switch", "code": "Code", "httpRequest": "HTTP Request", "wait": "Wait",
            "webhook": "Webhook trigger", "manualTrigger": "Manual trigger", "executeWorkflowTrigger": "Sub-workflow trigger",
            "stopAndError": "Stop and Error"}.get(t, t)


def output_names(node):
    t = node["type"].split(".")[-1]
    if t == "if":
        return ["true", "false"]
    if t == "switch":
        return [r.get("outputKey", str(i)) for i, r in enumerate(node["parameters"].get("rules", {}).get("values", []))]
    if node.get("onError") == "continueErrorOutput":
        return ["ok", "error"]
    return []


def main():
    with open(os.path.join(ROOT, "Main Workflow.json"), encoding="utf-8") as f:
        wf = json.load(f)
    nodes = {n["name"]: n for n in wf["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"}
    outs, ins = {}, {}
    for src, c in wf["connections"].items():
        for i, branch in enumerate(c.get("main", [])):
            for tgt in branch or []:
                outs.setdefault(src, []).append((i, tgt["node"]))
                ins.setdefault(tgt["node"], []).append((src, i))

    section_of = {name: s for s, names in SECTIONS.items() for name in names}
    missing = sorted(set(nodes) - set(section_of))
    unknown = sorted(set(section_of) - set(nodes)) + sorted(set(CONTEXT) - set(nodes))
    if missing or unknown:
        raise SystemExit(f"Section table out of date.\n  no section: {missing}\n  not in workflow: {unknown}")

    notes = {}
    for name, node in nodes.items():
        parts = [f"{section_of[name]} · {describe(node, ins.get(name, []))}."]
        if name in CONTEXT:
            parts.append(CONTEXT[name])
        names = output_names(node)
        if ins.get(name):
            parts.append("in: " + ", ".join(dict.fromkeys(s for s, _ in ins[name])))
        if outs.get(name):
            labelled = [(f"[{names[i]}]" if i < len(names) and len(names) > 1 else "") + t for i, t in outs[name]]
            parts.append("out: " + ", ".join(dict.fromkeys(labelled)))
        elif node["type"].split(".")[-1] not in ("stickyNote",) and not section_of[name].startswith(("TEMPLATE", "LEGACY", "DEBUG")):
            parts.append("out: (end of run)")
        notes[node["id"]] = {"node": name, "note": " | ".join(parts)}

    path = os.path.join(ROOT, "n8n_node_notes.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data[WORKFLOW_ID] = {"workflow": wf["name"], "notes": notes}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    longest = max(notes.values(), key=lambda v: len(v["note"]))
    print(f"{len(notes)} notes written; longest {len(longest['note'])} chars ({longest['node']})")


if __name__ == "__main__":
    main()
