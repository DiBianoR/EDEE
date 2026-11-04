"""
Write per-node notes ("road signs") into the live n8n workflows from n8n_node_notes.json.

    py -3 n8n_notes.py check              # report only: changes pending, unnoted nodes, stale entries
    py -3 n8n_notes.py apply              # write every workflow in the notes file
    py -3 n8n_notes.py apply <workflowId> # write just one

Why a separate script: the MCP update tool can't set the notes of an existing node, but
the n8n public REST API (PUT /api/v1/workflows/{id}) can. The PUT replaces the whole
workflow, so this script never builds one by hand. It fetches the live workflow, changes
ONLY `notes` / `notesInFlow` on the nodes listed in the file, sends everything else back
exactly as fetched, then re-fetches and refuses to call it done unless notes are the
only difference.

Safety:
  * Always `publishIfActive=false`. Without it n8n re-publishes a published workflow on
    every PUT, i.e. the change goes straight to production. With it the change is saved
    as a draft and the published version keeps running until someone publishes.
  * n8n_backup.py runs before and after every apply; a failed backup aborts the apply.
  * The body's versionId is read-only in the API, so the script checks for concurrent
    edits itself: it re-reads the workflow just before writing and aborts if the
    versionId moved since the notes were computed.

Notes file shape (keyed by node ID, so renames don't orphan a note; the names are there
for humans and are refreshed by every apply):
    { "<workflowId>": { "workflow": "EDEE-main",
                        "notes": { "<nodeId>": { "node": "cfg25", "note": "..." } } } }
A node absent from the file keeps whatever note it already has.

Auth: N8N_API_KEY (Settings → n8n API). Runs the backup with N8N_MCP_TOKEN.
"""

import json
import os
import subprocess
import sys

import requests

API = "https://edee.app.n8n.cloud/api/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
NOTES_FILE = os.path.join(HERE, "n8n_node_notes.json")

# The update endpoint rejects unknown properties (additionalProperties: false), so the
# body is built from these lists rather than from whatever GET happened to return.
NODE_FIELDS = ("id", "name", "webhookId", "disabled", "notesInFlow", "notes", "type",
               "typeVersion", "executeOnce", "alwaysOutputData", "retryOnFail", "maxTries",
               "waitBetweenTries", "continueOnFail", "onError", "position", "parameters",
               "credentials", "customTelemetryTags", "extendsCredential")
SETTINGS_FIELDS = ("saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution",
                   "saveDataSuccessExecution", "executionTimeout", "errorWorkflow", "timezone",
                   "executionOrder", "binaryMode", "engineType", "callerPolicy", "callerIds",
                   "timeSavedMode", "timeSavedPerExecution", "redactionPolicy",
                   "redactCallerExecutions", "availableInMCP", "customTelemetryTags")
STICKY = "n8n-nodes-base.stickyNote"


def api(method, path, **kwargs):
    r = requests.request(method, f"{API}{path}", timeout=120,
                         headers={"X-N8N-API-KEY": os.environ["N8N_API_KEY"]}, **kwargs)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:500]}")
    r.encoding = "utf-8"
    return r.json()


def load_notes():
    with open(NOTES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_notes(data):
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def plan(workflow, entry):
    """What an apply would change: [(node, new_note)], plus coverage gaps for check."""
    by_id = {n["id"]: n for n in workflow["nodes"]}
    changes = []
    for node_id, item in entry.get("notes", {}).items():
        node = by_id.get(node_id)
        if node and (node.get("notes") != item["note"] or not node.get("notesInFlow")):
            changes.append((node, item["note"]))
    unnoted = [n["name"] for n in workflow["nodes"]
               if n["type"] != STICKY and n["id"] not in entry.get("notes", {})]
    stale = [f'{item.get("node")} ({node_id})' for node_id, item in entry.get("notes", {}).items()
             if node_id not in by_id]
    return changes, unnoted, stale


def strip_notes(workflow):
    """The workflow with every note removed, for the 'nothing else changed' comparison."""
    nodes = sorted(({k: v for k, v in n.items() if k not in ("notes", "notesInFlow")}
                    for n in workflow["nodes"]), key=lambda n: n["id"])
    return json.dumps({"nodes": nodes, "connections": workflow["connections"],
                       "settings": workflow.get("settings"), "name": workflow["name"],
                       "nodeGroups": workflow.get("nodeGroups")}, sort_keys=True)


def backup(label):
    result = subprocess.run([sys.executable, os.path.join(HERE, "n8n_backup.py"), label], cwd=HERE)
    if result.returncode != 0:
        sys.exit(f"Backup '{label}' failed; nothing was written to n8n.")


def apply_one(workflow_id, entry):
    before = api("GET", f"/workflows/{workflow_id}")
    changes, _, stale = plan(before, entry)
    if stale:
        print(f"  stale entries (node no longer exists): {', '.join(stale)}")
    if not changes:
        print(f"{before['name']}: notes already up to date")
        return

    wanted = {node["id"]: note for node, note in changes}
    nodes = []
    for node in before["nodes"]:
        node = {k: v for k, v in node.items() if k in NODE_FIELDS}
        if node["id"] in wanted:
            node["notes"] = wanted[node["id"]]
            node["notesInFlow"] = True
        nodes.append(node)
    body = {
        "name": before["name"],
        "nodes": nodes,
        "connections": before["connections"],
        "settings": {k: v for k, v in (before.get("settings") or {}).items() if k in SETTINGS_FIELDS},
        "staticData": before.get("staticData"),
        "pinData": before.get("pinData"),
        "nodeGroups": before.get("nodeGroups") or [],
    }

    # Last-moment concurrency check: the API ignores versionId in the body.
    if api("GET", f"/workflows/{workflow_id}")["versionId"] != before["versionId"]:
        sys.exit(f"{before['name']} changed while preparing the update; nothing written. Re-run.")

    api("PUT", f"/workflows/{workflow_id}", params={"publishIfActive": "false"}, json=body)
    after = api("GET", f"/workflows/{workflow_id}")

    problems = []
    if strip_notes(after) != strip_notes(before):
        problems.append("something other than notes changed")
    if after.get("activeVersionId") != before.get("activeVersionId"):
        problems.append(f"the PUBLISHED version moved ({before.get('activeVersionId')} -> {after.get('activeVersionId')})")
    after_notes = {n["id"]: n.get("notes") for n in after["nodes"]}
    missing = [node["name"] for node, note in changes if after_notes.get(node["id"]) != note]
    if missing:
        problems.append(f"notes did not stick on: {', '.join(missing)}")

    published = "unchanged" if after.get("activeVersionId") == before.get("activeVersionId") else "MOVED"
    print(f"{before['name']}: {len(changes)} note(s) written to the draft "
          f"(version {before['versionId'][:8]} -> {after['versionId'][:8]}; published version {published})")
    if problems:
        sys.exit("VERIFY FAILED: " + "; ".join(problems) +
                 ". Restore from the 'before notes' snapshot (restore_workflow_version or re-import).")

    # Refresh the human-readable names in the notes file.
    names = {n["id"]: n["name"] for n in after["nodes"]}
    entry["workflow"] = after["name"]
    for node_id, item in entry["notes"].items():
        item["node"] = names.get(node_id, item.get("node"))


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("check", "apply"):
        sys.exit(__doc__)
    if not os.environ.get("N8N_API_KEY"):
        sys.exit("N8N_API_KEY is not set.")
    data = load_notes()
    targets = sys.argv[2:] or list(data)

    if sys.argv[1] == "check":
        for workflow_id in targets:
            workflow = api("GET", f"/workflows/{workflow_id}")
            changes, unnoted, stale = plan(workflow, data[workflow_id])
            print(f"{workflow['name']}: {len(changes)} note(s) to write, "
                  f"{len(unnoted)} node(s) without an entry, {len(stale)} stale entr(ies)")
            for name in stale:
                print(f"  stale: {name}")
        return

    backup("before notes")
    for workflow_id in targets:
        apply_one(workflow_id, data[workflow_id])
    save_notes(data)
    backup("after notes")


if __name__ == "__main__":
    main()
