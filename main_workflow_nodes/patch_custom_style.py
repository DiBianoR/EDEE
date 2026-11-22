"""
Custom (typed-in) styles survive "Resume final review": patch EDEE-main's DRAFT.

    py -3 main_workflow_nodes/patch_custom_style.py

A custom style is style_preference "custom" plus config.custom_style (the converter's two
strings). Resume only ever carried the key, so three Code nodes change:
  * Send Final Broadcast / Resume: Restore Completed: the run_settings record also holds
    custom_style.
  * Resume: Load Job: reads it back and passes it to Load Config next to style_preference.
The two Resume nodes take their code from resume_load_job.js / resume_restore_completed.js
(the same sources patch_resume_final_gate.py uses); Send Final Broadcast gets one exact-text
edit, which patch_resume_final_gate.py's BROADCAST_EDITS already includes.

Re-runnable. REST PUT with publishIfActive=false, backups before and after, and it refuses
to call it done unless those three jsCode fields are the only difference.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from n8n_notes import api, backup, NODE_FIELDS, SETTINGS_FIELDS  # noqa: E402

WORKFLOW_ID = "HtwI6u1ae2xwKw1f"
FROM_FILE = {"Resume: Load Job": "resume_load_job.js",
             "Resume: Restore Completed": "resume_restore_completed.js"}
BROADCAST_OLD = ("        max_image_tier: config.maximum_image_tier ?? null\n"
                 "    }) }],\n")
BROADCAST_NEW = ("        max_image_tier: config.maximum_image_tier ?? null,\n"
                 "        custom_style: config.custom_style ?? null\n"
                 "    }) }],\n")


def read(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def main():
    before = api("GET", f"/workflows/{WORKFLOW_ID}")
    by = {n["name"]: n for n in before["nodes"]}
    new_code = {name: read(fn) for name, fn in FROM_FILE.items()}
    bc = by["Send Final Broadcast"]["parameters"]["jsCode"]
    if BROADCAST_NEW not in bc:
        if bc.count(BROADCAST_OLD) != 1:
            sys.exit("Send Final Broadcast: the run_settings block is not what this patch expects.")
        bc = bc.replace(BROADCAST_OLD, BROADCAST_NEW)
    new_code["Send Final Broadcast"] = bc
    if all(by[n]["parameters"]["jsCode"] == c for n, c in new_code.items()):
        print("EDEE-main already has the custom-style resume edits; nothing to do.")
        return

    backup("before custom style resume")
    nodes = []
    for n in before["nodes"]:
        n = {k: v for k, v in n.items() if k in NODE_FIELDS}
        if n["name"] in new_code:
            n["parameters"] = {**n["parameters"], "jsCode": new_code[n["name"]]}
        nodes.append(n)
    body = {
        "name": before["name"], "nodes": nodes, "connections": before["connections"],
        "settings": {k: v for k, v in (before.get("settings") or {}).items() if k in SETTINGS_FIELDS},
        "staticData": before.get("staticData"), "pinData": before.get("pinData"),
        "nodeGroups": before.get("nodeGroups") or [],
    }
    if api("GET", f"/workflows/{WORKFLOW_ID}")["versionId"] != before["versionId"]:
        sys.exit("EDEE-main changed while preparing the patch; nothing written. Re-run.")
    api("PUT", f"/workflows/{WORKFLOW_ID}", params={"publishIfActive": "false"}, json=body)
    after = api("GET", f"/workflows/{WORKFLOW_ID}")

    def strip(w):
        out = []
        for n in sorted(w["nodes"], key=lambda n: n["id"]):
            p = dict(n["parameters"])
            if n["name"] in new_code:
                p.pop("jsCode", None)
            out.append({**{k: v for k, v in n.items() if k != "parameters"}, "parameters": p})
        return json.dumps(out + [w["connections"]], sort_keys=True)

    after_by = {n["name"]: n for n in after["nodes"]}
    problems = [f"{n}: jsCode did not stick" for n, c in new_code.items()
                if after_by[n]["parameters"].get("jsCode") != c]
    if strip(after) != strip(before):
        problems.append("something other than the three jsCode fields changed")
    if after.get("activeVersionId") != before.get("activeVersionId"):
        problems.append("the PUBLISHED version moved")
    print(f"EDEE-main draft {before['versionId'][:8]} -> {after['versionId'][:8]}; "
          f"published {str(after.get('activeVersionId'))[:8]}")
    if problems:
        sys.exit("VERIFY FAILED: " + "; ".join(problems))
    backup("after custom style resume")


if __name__ == "__main__":
    main()
