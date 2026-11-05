"""
Add "Resume final review" to EDEE-main's DRAFT (never published by this script).

    py -3 main_workflow_nodes/patch_resume_final_gate.py

Re-runnable: nodes that already exist get their code/parameters refreshed from the
resume_*.js sources instead of being added twice. Uses the REST API (like n8n_notes.py)
because the new "Set API Key (resume)" copies the live keys node, and this keeps the
keys out of any tool call or chat. Always publishIfActive=false; backs up before and
after; verifies afterwards that only the intended nodes and connections changed.

What it does (STRUCTURE.md, "Resume final review"):
  new chain   Resume Trigger → Resume: Load Job → Resume: Respond OK → Set API Key (resume)
              → Load Resume Config → Resume: Rebuild State → Final Gate: Announce
  new exits   After final log [continue] → Resume: keep previous? (continue)
                  [true] → Resume: Restore Completed   [false] → Phase 5 - Output
              After final log [abort]    → Resume: keep previous? (abort)
                  [true] → Resume: Restore Completed   [false] → cfg final abort
  redraw      After final log [rework]   → Resume: redraw scaffold?
                  [true]  → Resume: Prepare Redraw → cfg amend description
                            → image_detail_planner - amend_description → Route Workflow3
                  [false] → Final corrections → reset retries
              After human log [abort]    → Resume: keep previous? (scaffold abort)
                  [true] → Resume: Restore Completed   [false] → cfg human abort
  edits       cfg25 / cfg26: scaffold falls back to Resume: Rebuild State
              cfg final review: after a resume marker, uses the *_resume task variants and
                  counts final_review turns from that marker (normal runs: unchanged)
              cfg human review: counts human_review turns after the last scaffold_redo
                  marker (normal runs: unchanged)
              Send Final Broadcast: appends the run_settings record
"""

import copy
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from n8n_notes import api, backup, NODE_FIELDS, SETTINGS_FIELDS  # noqa: E402

WORKFLOW_ID = "HtwI6u1ae2xwKw1f"


def code(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


SCAFFOLD = ("={{ $('Prepare for Vision').isExecuted ? $('Prepare for Vision').item.json.%s"
            " : ($('Resume: Rebuild State').isExecuted ? $('Resume: Rebuild State').first(0, 0).json.%s : '') }}")
# No resume marker → start = -1 → the whole history and the plain task names, i.e. exactly
# what these expressions returned before the resume feature existed.
FINAL_REVIEW_TASK = ("={{ (() => { const ev = $json.session_events;"
                     " const start = ev.map(e => e.task).lastIndexOf('resume_final_gate');"
                     " const reply = ev.slice(start + 1).some(e => e.author === 'issue_aggregator' && String(e.task).startsWith('final_review'));"
                     " return (reply ? 'final_review_reply' : 'final_review_open') + (start >= 0 ? '_resume' : ''); })() }}")
HUMAN_REVIEW_TASK = ("={{ (() => { const ev = $json.session_events;"
                     " const start = ev.map(e => e.task).lastIndexOf('scaffold_redo');"
                     " return ev.slice(start + 1).some(e => e.author === 'inspection_manager' && String(e.task).startsWith('human_review'))"
                     " ? 'human_review_reply' : 'human_review_open'; })() }}")
KEEP_CONTINUE = "={{ $('Resume: Rebuild State').isExecuted && !$('artist - render_final').isExecuted }}"
KEEP_ABORT = "={{ $('Resume: Rebuild State').isExecuted }}"
REDRAW = "={{ $('Resume: Rebuild State').isExecuted && $json.session_state.redraw_scaffold === true }}"
REDRAW_CHAIN = ["Resume: Prepare Redraw", "cfg amend description", "image_detail_planner - amend_description", "Route Workflow3"]


def if_params(template, expression):
    p = copy.deepcopy(template["parameters"])
    cond = p["conditions"]["conditions"][0]
    cond["leftValue"] = expression
    cond["id"] = str(uuid.uuid4())
    return p


def cfg_params(template, phase, agent, task):
    """A standard cfg Set node (PHASE_ID / AGENT_ID / TASK_ID, include other fields)."""
    p = copy.deepcopy(template["parameters"])
    values = {"PHASE_ID": phase, "AGENT_ID": agent, "TASK_ID": task}
    p["assignments"]["assignments"] = [a for a in p["assignments"]["assignments"] if a["name"] in values]
    for a in p["assignments"]["assignments"]:
        a["value"] = values[a["name"]]
    if {a["name"] for a in p["assignments"]["assignments"]} != set(values):
        raise SystemExit(f"cfg template {template['name']} lacks PHASE_ID/AGENT_ID/TASK_ID")
    return p


def build_new_nodes(nodes):
    by = {n["name"]: n for n in nodes}
    api_trigger, keys, load_cfg, if_tpl = by["API Trigger"], by["Set API Key"], by["Load Phase 1 Config"], by["Final review?"]
    cfg_tpl, ua_tpl = by["cfg32"], by["image_detail_planner - review_description"]
    code_v = by["Final Gate: Announce"]["typeVersion"]
    return [
        {"name": "Resume Trigger", "type": "n8n-nodes-base.webhook", "typeVersion": api_trigger["typeVersion"],
         "position": [3088, 384], "webhookId": str(uuid.uuid4()), "credentials": copy.deepcopy(api_trigger.get("credentials")),
         "parameters": {"httpMethod": "POST", "path": "resume-final-gate", "authentication": "headerAuth",
                        "responseMode": "responseNode", "options": {}}},
        {"name": "Resume: Load Job", "type": "n8n-nodes-base.code", "typeVersion": code_v, "position": [3264, 384],
         "parameters": {"jsCode": code("resume_load_job.js")}},
        {"name": "Resume: Respond OK", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5, "position": [3424, 384],
         "parameters": {"respondWith": "json",
                        "responseBody": "={{ JSON.stringify({ ok: true, job_id: $json.job_id, style_preference: $json.style_preference, style_source: $json.resume.style_source }) }}",
                        "options": {}}},
        {"name": "Set API Key (resume)", "type": keys["type"], "typeVersion": keys["typeVersion"], "position": [3584, 384],
         "parameters": copy.deepcopy(keys["parameters"])},
        {"name": "Load Resume Config", "type": load_cfg["type"], "typeVersion": load_cfg["typeVersion"], "position": [3744, 384],
         "parameters": copy.deepcopy(load_cfg["parameters"])},
        {"name": "Resume: Rebuild State", "type": "n8n-nodes-base.code", "typeVersion": code_v, "position": [3904, 384],
         "parameters": {"jsCode": code("resume_rebuild_state.js")}},
        {"name": "Resume: keep previous? (continue)", "type": if_tpl["type"], "typeVersion": if_tpl["typeVersion"],
         "position": [5376, 1040], "parameters": if_params(if_tpl, KEEP_CONTINUE)},
        {"name": "Resume: keep previous? (abort)", "type": if_tpl["type"], "typeVersion": if_tpl["typeVersion"],
         "position": [5376, 1216], "parameters": if_params(if_tpl, KEEP_ABORT)},
        {"name": "Resume: Restore Completed", "type": "n8n-nodes-base.code", "typeVersion": code_v, "position": [5584, 1120],
         "parameters": {"jsCode": code("resume_restore_completed.js")}},
        # --- redraw the base diagram (resumed jobs only) ---
        {"name": "Resume: redraw scaffold?", "type": if_tpl["type"], "typeVersion": if_tpl["typeVersion"],
         "position": [5296, 528], "parameters": if_params(if_tpl, REDRAW)},
        {"name": "Resume: Prepare Redraw", "type": "n8n-nodes-base.code", "typeVersion": code_v, "position": [5456, 368],
         "parameters": {"jsCode": code("resume_prepare_redraw.js")}},
        {"name": "cfg amend description", "type": cfg_tpl["type"], "typeVersion": cfg_tpl["typeVersion"], "position": [5616, 368],
         "parameters": cfg_params(cfg_tpl, "2", "image_detail_planner", "amend_description")},
        {"name": "image_detail_planner - amend_description", "type": ua_tpl["type"], "typeVersion": ua_tpl["typeVersion"],
         "position": [5776, 368], "parameters": copy.deepcopy(ua_tpl["parameters"])},
        {"name": "Resume: keep previous? (scaffold abort)", "type": if_tpl["type"], "typeVersion": if_tpl["typeVersion"],
         "position": [2560, 592], "parameters": if_params(if_tpl, KEEP_ABORT)},
    ]


def link(conns, src, out, dst):
    branches = conns.setdefault(src, {}).setdefault("main", [])
    while len(branches) <= out:
        branches.append([])
    if not any(t["node"] == dst for t in branches[out]):
        branches[out].append({"node": dst, "type": "main", "index": 0})


def unlink(conns, src, out, dst):
    branches = conns.get(src, {}).get("main", [])
    if out < len(branches):
        branches[out] = [t for t in branches[out] if t["node"] != dst]


# Send Final Broadcast: append a run_settings event to the finished transcript, recording
# the style (and tiers) that Load Config baked in, so "Resume: Load Job" can read it back
# instead of inferring it. Exact-text replacements on the live code; skipped once applied.
BROADCAST_EDITS = [
    ("const { config = {}, session_state: ss = {}, session_events: events = [] } =\n"
     "    latestRun('Phase 6 - Output') || {};\n",
     "const { config = {}, session_state: ss = {}, session_events: rawEvents = [] } =\n"
     "    latestRun('Phase 6 - Output') || {};\n"
     "\n"
     "// === 🏷️ RUN SETTINGS — launch choices baked into the config and otherwise lost ===\n"
     "// \"Resume final review\" (Resume: Load Job) reads the LAST run_settings event to rebuild\n"
     "// the config with the same style. Author \"run_settings\" is in no agent's history_scope,\n"
     "// so no agent ever sees it.\n"
     "const runSettingsEvent = {\n"
     "    author: \"run_settings\",\n"
     "    task: \"run_settings\",\n"
     "    status: \"ok\",\n"
     "    parts: [{ text: JSON.stringify({\n"
     "        style_preference: config.style_preference ?? null,\n"
     "        max_text_tier: config.maximum_text_tier ?? null,\n"
     "        max_image_tier: config.maximum_image_tier ?? null\n"
     "    }) }],\n"
     "    timestamp: new Date().toISOString(),\n"
     "    model: \"none\",\n"
     "    cost: 0\n"
     "};\n"
     "const events = [...rawEvents, runSettingsEvent];\n"),
    ("    session_events: events,\n    user_message: ss.user_message ?? null,",
     "    session_events: events,\n    events: [runSettingsEvent],       // into the doc's working transcript too\n"
     "    user_message: ss.user_message ?? null,"),
]


def patch_broadcast(node):
    code = node["parameters"]["jsCode"]
    if "runSettingsEvent" in code:
        return False
    for old, new in BROADCAST_EDITS:
        if code.count(old) != 1:
            raise SystemExit(f"Send Final Broadcast: expected exactly one occurrence of:\n{old}")
        code = code.replace(old, new)
    node["parameters"]["jsCode"] = code
    return True


def set_assignment(node, field, value):
    for a in node["parameters"]["assignments"]["assignments"]:
        if a["name"] == field:
            a["value"] = value
            return
    raise SystemExit(f"{node['name']} has no '{field}' assignment")


def main():
    backup("before resume final review")
    before = api("GET", f"/workflows/{WORKFLOW_ID}")
    nodes = copy.deepcopy(before["nodes"])
    conns = copy.deepcopy(before["connections"])
    by = {n["name"]: n for n in nodes}

    added = []
    for new in build_new_nodes(before["nodes"]):
        if new["name"] in by:   # re-run: refresh code/params, keep id, position and webhookId
            old_conds = by[new["name"]]["parameters"].get("conditions", {}).get("conditions")
            if old_conds:       # IF nodes: keep the existing condition id, so a re-run doesn't churn it
                new["parameters"]["conditions"]["conditions"][0]["id"] = old_conds[0]["id"]
            by[new["name"]]["parameters"] = new["parameters"]
        else:
            new["id"] = str(uuid.uuid4())
            nodes.append(new)
            by[new["name"]] = new
            added.append(new["name"])

    for name in ("cfg25", "cfg26"):
        set_assignment(by[name], "base64_img_string", SCAFFOLD % ("base64_img_string", "resume_scaffold_b64"))
        set_assignment(by[name], "base64_img_string_mime", SCAFFOLD % ("base64_img_string_mime", "resume_scaffold_mime"))
    set_assignment(by["cfg final review"], "TASK_ID", FINAL_REVIEW_TASK)
    human_task = next(a["value"] for a in by["cfg human review"]["parameters"]["assignments"]["assignments"] if a["name"] == "TASK_ID")
    if "inspection_manager" not in human_task or "human_review_reply" not in human_task:
        raise SystemExit(f"cfg human review TASK_ID is not what this patch expects: {human_task}")
    set_assignment(by["cfg human review"], "TASK_ID", HUMAN_REVIEW_TASK)
    patch_broadcast(by["Send Final Broadcast"])

    chain = ["Resume Trigger", "Resume: Load Job", "Resume: Respond OK", "Set API Key (resume)",
             "Load Resume Config", "Resume: Rebuild State", "Final Gate: Announce"]
    for src, dst in zip(chain, chain[1:]):
        link(conns, src, 0, dst)
    unlink(conns, "After final log", 0, "Phase 5 - Output")
    link(conns, "After final log", 0, "Resume: keep previous? (continue)")
    link(conns, "Resume: keep previous? (continue)", 0, "Resume: Restore Completed")
    link(conns, "Resume: keep previous? (continue)", 1, "Phase 5 - Output")
    unlink(conns, "After final log", 3, "cfg final abort")
    link(conns, "After final log", 3, "Resume: keep previous? (abort)")
    link(conns, "Resume: keep previous? (abort)", 0, "Resume: Restore Completed")
    link(conns, "Resume: keep previous? (abort)", 1, "cfg final abort")
    # Redraw the base diagram: a confirmed redraw request leaves the final gate as "rework".
    unlink(conns, "After final log", 1, "Final corrections → reset retries")
    link(conns, "After final log", 1, "Resume: redraw scaffold?")
    link(conns, "Resume: redraw scaffold?", 0, "Resume: Prepare Redraw")
    link(conns, "Resume: redraw scaffold?", 1, "Final corrections → reset retries")
    for src, dst in zip(REDRAW_CHAIN, REDRAW_CHAIN[1:]):
        link(conns, src, 0, dst)
    # A stop at the redo's scaffold gate keeps the previous result, like a stop at the final gate.
    unlink(conns, "After human log", 3, "cfg human abort")
    link(conns, "After human log", 3, "Resume: keep previous? (scaffold abort)")
    link(conns, "Resume: keep previous? (scaffold abort)", 0, "Resume: Restore Completed")
    link(conns, "Resume: keep previous? (scaffold abort)", 1, "cfg human abort")

    body = {
        "name": before["name"],
        "nodes": [{k: v for k, v in n.items() if k in NODE_FIELDS} for n in nodes],
        "connections": conns,
        "settings": {k: v for k, v in (before.get("settings") or {}).items() if k in SETTINGS_FIELDS},
        "staticData": before.get("staticData"),
        "pinData": before.get("pinData"),
        "nodeGroups": before.get("nodeGroups") or [],
    }
    if api("GET", f"/workflows/{WORKFLOW_ID}")["versionId"] != before["versionId"]:
        sys.exit("EDEE-main changed while preparing the patch; nothing written. Re-run.")
    api("PUT", f"/workflows/{WORKFLOW_ID}", params={"publishIfActive": "false"}, json=body)
    after = api("GET", f"/workflows/{WORKFLOW_ID}")

    # Verify: every pre-existing node is unchanged except the ones edited above.
    expected = ({"cfg25", "cfg26", "cfg final review", "cfg human review", "Send Final Broadcast"}
                | {n["name"] for n in build_new_nodes(before["nodes"])})
    old = {n["id"]: n for n in before["nodes"]}
    changed = [n["name"] for n in after["nodes"] if n["id"] in old
               and json.dumps(n.get("parameters"), sort_keys=True) != json.dumps(old[n["id"]].get("parameters"), sort_keys=True)]
    problems = []
    if set(changed) - expected:
        problems.append(f"unexpected parameter changes: {sorted(set(changed) - expected)}")
    missing = [n["name"] for n in build_new_nodes(before["nodes"]) if n["name"] not in {x["name"] for x in after["nodes"]}]
    if missing:
        problems.append(f"nodes missing after save: {missing}")
    if after.get("activeVersionId") != before.get("activeVersionId"):
        problems.append("the PUBLISHED version moved")
    print(f"added {len(added)} node(s): {added}; edited: {sorted(set(changed) & expected)}; "
          f"draft {before['versionId'][:8]} -> {after['versionId'][:8]}; published {after.get('activeVersionId', '')[:8]}")
    if problems:
        sys.exit("VERIFY FAILED: " + "; ".join(problems))
    backup("after resume final review")


if __name__ == "__main__":
    main()
