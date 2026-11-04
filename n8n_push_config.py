"""
Push config.js into the Load Config sub-workflow's DRAFT (the "Load Config" Code node).

    py -3 n8n_push_config.py

Replaces only that node's jsCode with the repo's config.js, via the REST API with
publishIfActive=false, and verifies nothing else changed. Backs up before and after.
Every run calls Load Config, so publishing the result is a separate, deliberate step.
"""

import json
import os
import sys

from n8n_notes import api, backup, NODE_FIELDS, SETTINGS_FIELDS

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_ID = "59k24tT721u3gsni"
NODE = "Load Config"


def main():
    with open(os.path.join(HERE, "config.js"), encoding="utf-8") as f:
        code = f.read()
    before = api("GET", f"/workflows/{WORKFLOW_ID}")
    node = next((n for n in before["nodes"] if n["name"] == NODE), None)
    if node is None:
        sys.exit(f"No '{NODE}' node in workflow {WORKFLOW_ID}")
    if node["parameters"].get("jsCode") == code:
        print("Load Config already matches config.js; nothing to do.")
        return

    backup("before config.js push")
    nodes = []
    for n in before["nodes"]:
        n = {k: v for k, v in n.items() if k in NODE_FIELDS}
        if n["name"] == NODE:
            n["parameters"] = {**n["parameters"], "jsCode": code}
        nodes.append(n)
    body = {
        "name": before["name"], "nodes": nodes, "connections": before["connections"],
        "settings": {k: v for k, v in (before.get("settings") or {}).items() if k in SETTINGS_FIELDS},
        "staticData": before.get("staticData"), "pinData": before.get("pinData"),
        "nodeGroups": before.get("nodeGroups") or [],
    }
    if api("GET", f"/workflows/{WORKFLOW_ID}")["versionId"] != before["versionId"]:
        sys.exit("Load Config changed while preparing the push; nothing written. Re-run.")
    api("PUT", f"/workflows/{WORKFLOW_ID}", params={"publishIfActive": "false"}, json=body)
    after = api("GET", f"/workflows/{WORKFLOW_ID}")

    strip = lambda w: json.dumps(sorted(({k: v for k, v in n.items() if k != "parameters"} | {
        "parameters": {k: v for k, v in n["parameters"].items() if k != "jsCode"}} for n in w["nodes"]),
        key=lambda n: n["id"]) + [w["connections"]], sort_keys=True)
    pushed = next(n for n in after["nodes"] if n["name"] == NODE)["parameters"]["jsCode"]
    problems = []
    if pushed != code:
        problems.append("jsCode did not stick")
    if strip(after) != strip(before):
        problems.append("something other than the jsCode changed")
    if after.get("activeVersionId") != before.get("activeVersionId"):
        problems.append("the PUBLISHED version moved")
    print(f"Load Config draft {before['versionId'][:8]} -> {after['versionId'][:8]}; "
          f"published {str(after.get('activeVersionId'))[:8]}")
    if problems:
        sys.exit("VERIFY FAILED: " + "; ".join(problems))
    backup("after config.js push")


if __name__ == "__main__":
    main()
