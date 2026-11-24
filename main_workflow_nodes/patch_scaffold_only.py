"""
"Redraw the base diagram only": patch EDEE-main's DRAFT (never published by this script).

    py -3 main_workflow_nodes/patch_scaffold_only.py

A resumed job's base-diagram redraw used to repaint the illustration every time. Now the
Final Gatekeeper sets keep_final (config.js, RESUME_CHANGE_ROUTES route c), and:
  * new IF "Resume: scaffold only?" between Archive Scaffolding and Phase 3 - Output:
        [true]  resumed job and keep_final → Resume: Restore Completed
        [false] → Phase 3 - Output (every normal run, and a redraw that repaints too)
  * Resume: Prepare Redraw (resume_prepare_redraw.js): reads keep_final, arms the painting
    directives only when repainting, and arms the revision framing with the approved code.
  * Resume: Restore Completed (resume_restore_completed.js): the scaffold-only ending.

Re-runnable. REST PUT with publishIfActive=false, backups before and after, and it refuses
to call it done unless exactly these nodes and connections changed.
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
FROM_FILE = {"Resume: Prepare Redraw": "resume_prepare_redraw.js",
             "Resume: Restore Completed": "resume_restore_completed.js"}
IF_NAME = "Resume: scaffold only?"
IF_TEMPLATE = "Resume: keep previous? (abort)"
SCAFFOLD_ONLY = "={{ $('Resume: Rebuild State').isExecuted && $json.session_state.keep_final === true }}"


def read(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def main():
    before = api("GET", f"/workflows/{WORKFLOW_ID}")
    nodes = copy.deepcopy(before["nodes"])
    conns = copy.deepcopy(before["connections"])
    by = {n["name"]: n for n in nodes}

    for name, fn in FROM_FILE.items():
        by[name]["parameters"] = {**by[name]["parameters"], "jsCode": read(fn)}

    if IF_NAME not in by:
        tpl = by[IF_TEMPLATE]
        node = {"id": str(uuid.uuid4()), "name": IF_NAME, "type": tpl["type"],
                "typeVersion": tpl["typeVersion"], "position": [2928, 1472],
                "parameters": copy.deepcopy(tpl["parameters"])}
        node["parameters"]["conditions"]["conditions"][0]["id"] = str(uuid.uuid4())
        nodes.append(node)
        by[IF_NAME] = node
    by[IF_NAME]["parameters"]["conditions"]["conditions"][0]["leftValue"] = SCAFFOLD_ONLY

    conns["Archive Scaffolding"] = {"main": [[{"node": IF_NAME, "type": "main", "index": 0}]]}
    conns[IF_NAME] = {"main": [[{"node": "Resume: Restore Completed", "type": "main", "index": 0}],
                               [{"node": "Phase 3 - Output", "type": "main", "index": 0}]]}

    if json.dumps([nodes, conns], sort_keys=True) == json.dumps([before["nodes"], before["connections"]], sort_keys=True):
        print("EDEE-main already has the scaffold-only edits; nothing to do.")
        return

    backup("before scaffold only redraw")
    body = {
        "name": before["name"],
        "nodes": [{k: v for k, v in n.items() if k in NODE_FIELDS} for n in nodes],
        "connections": conns,
        "settings": {k: v for k, v in (before.get("settings") or {}).items() if k in SETTINGS_FIELDS},
        "staticData": before.get("staticData"), "pinData": before.get("pinData"),
        "nodeGroups": before.get("nodeGroups") or [],
    }
    if api("GET", f"/workflows/{WORKFLOW_ID}")["versionId"] != before["versionId"]:
        sys.exit("EDEE-main changed while preparing the patch; nothing written. Re-run.")
    api("PUT", f"/workflows/{WORKFLOW_ID}", params={"publishIfActive": "false"}, json=body)
    after = api("GET", f"/workflows/{WORKFLOW_ID}")

    problems = []
    old = {n["name"]: n for n in before["nodes"]}
    new = {n["name"]: n for n in after["nodes"]}
    expected = set(FROM_FILE) | {IF_NAME}
    changed = {n for n in new if n not in old or
               json.dumps(new[n].get("parameters"), sort_keys=True) != json.dumps(old[n].get("parameters"), sort_keys=True)}
    if changed - expected:
        problems.append(f"unexpected node changes: {sorted(changed - expected)}")
    if set(old) - set(new):
        problems.append(f"nodes disappeared: {sorted(set(old) - set(new))}")
    for name, fn in FROM_FILE.items():
        if new[name]["parameters"].get("jsCode") != read(fn):
            problems.append(f"{name}: jsCode did not stick")
    oc, nc = before["connections"], after["connections"]
    conn_changed = {k for k in set(oc) | set(nc) if json.dumps(oc.get(k), sort_keys=True) != json.dumps(nc.get(k), sort_keys=True)}
    if conn_changed != {"Archive Scaffolding", IF_NAME} and conn_changed - {"Archive Scaffolding", IF_NAME}:
        problems.append(f"unexpected connection changes: {sorted(conn_changed)}")
    if after.get("activeVersionId") != before.get("activeVersionId"):
        problems.append("the PUBLISHED version moved")
    print(f"EDEE-main draft {before['versionId'][:8]} -> {after['versionId'][:8]}; "
          f"published {str(after.get('activeVersionId'))[:8]}")
    if problems:
        sys.exit("VERIFY FAILED: " + "; ".join(problems))
    backup("after scaffold only redraw")


if __name__ == "__main__":
    main()
