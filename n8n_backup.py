"""
Back up the live n8n workflows through the instance MCP server.

    py -3 n8n_backup.py "before road-sign comments"

For every workflow on the instance it fetches the full draft (nodes, connections,
settings, versionId, activeVersionId) and writes it in two places:

  1. The repo mirror, e.g. "Main Workflow.json" — overwritten each run, so `git diff`
     shows exactly what changed in n8n since the last commit.
  2. n8n_backups/<timestamp>_<label>/ — a local, git-ignored snapshot that is never
     overwritten, so a rollback point exists even between commits.

Each run appends one entry to n8n_versions.md: per workflow, its id, the version id
and version name n8n gave the current draft, and whether production (the published
version) still matches the draft. Restore a version with the MCP tool
restore_workflow_version, or re-import the snapshot JSON in the n8n editor.

Auth: the same token Claude uses, read from the N8N_MCP_TOKEN environment variable.
The snapshots contain the API keys from the "Set API Key" nodes; the pre-commit hook
redacts them from the repo mirror, and n8n_backups/ is git-ignored.
"""

import json
import os
import re
import sys
from datetime import datetime

import requests

MCP_URL = "https://edee.app.n8n.cloud/mcp-server/http"
HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_ROOT = os.path.join(HERE, "n8n_backups")
VERSION_LOG = os.path.join(HERE, "n8n_versions.md")

# Repo filenames predate this script; keep them so git history stays continuous.
# A workflow not listed here is mirrored under its own n8n name.
REPO_FILES = {
    "HtwI6u1ae2xwKw1f": "Main Workflow.json",
    "UReLQDgBIPpb9P4f": "Universal Agent Sub-Workflow.json",
    "59k24tT721u3gsni": "Load Config Sub-workflow.json",
    "So6gmhxxUKhYV8oS": "Test Orchestrator.json",
    "ef3RGC7luZyHzWgs": "EDEE Global Error Handler.json",
    "o7uM375uJZDnly9t": "Control.json",
}


class Mcp:
    """Minimal MCP client over streamable HTTP: initialize once, then tools/call."""

    def __init__(self, url, token):
        self.url = url
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.next_id = 1
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "edee-n8n-backup", "version": "1"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post(self, body):
        r = requests.post(self.url, headers=self.headers, json=body, timeout=120)
        r.raise_for_status()
        if "Mcp-Session-Id" in r.headers:
            self.headers["Mcp-Session-Id"] = r.headers["Mcp-Session-Id"]
        return r

    def _rpc(self, method, params):
        req_id = self.next_id
        self.next_id += 1
        r = self._post({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        # The server may answer as plain JSON or as an SSE stream of `data:` lines.
        if r.headers.get("Content-Type", "").startswith("text/event-stream"):
            messages = [json.loads(line[5:]) for line in r.text.splitlines()
                        if line.startswith("data:") and line[5:].strip()]
        else:
            messages = [r.json()]
        for msg in messages:
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]
        raise RuntimeError(f"{method}: no response with id {req_id}")

    def tool(self, name, **args):
        result = self._rpc("tools/call", {"name": name, "arguments": args})
        text = "".join(c.get("text", "") for c in result.get("content", []))
        if result.get("isError"):
            raise RuntimeError(f"{name}: {text}")
        return json.loads(text)


def safe_name(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "workflow"


def main():
    label = " ".join(sys.argv[1:]).strip() or "manual"
    token = os.environ.get("N8N_MCP_TOKEN")
    if not token:
        sys.exit("N8N_MCP_TOKEN is not set.")

    mcp = Mcp(MCP_URL, token)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    snapshot_dir = os.path.join(SNAPSHOT_ROOT, f"{stamp}_{safe_name(label)}")
    os.makedirs(snapshot_dir, exist_ok=True)

    rows = []
    for wf in mcp.tool("search_workflows", limit=200)["data"]:
        workflow = mcp.tool("get_workflow_details", workflowId=wf["id"])["workflow"]
        history = mcp.tool("get_workflow_history", workflowId=wf["id"], limit=1)["versions"]
        version = next((v for v in history if v["versionId"] == workflow["versionId"]), {})

        filename = REPO_FILES.get(wf["id"], f"{safe_name(workflow['name'])}.json")
        text = json.dumps(workflow, indent=2, ensure_ascii=False) + "\n"
        for path in (os.path.join(HERE, filename), os.path.join(snapshot_dir, filename)):
            with open(path, "w", encoding="utf-8") as f:   # CRLF, matching core.autocrlf
                f.write(text)

        if not workflow.get("activeVersionId"):
            published = "not published"
        elif workflow["activeVersionId"] == workflow["versionId"]:
            published = "same as draft"
        else:
            published = f"`{workflow['activeVersionId']}` (draft differs)"
        rows.append((workflow["name"], wf["id"], workflow["versionId"],
                     version.get("name") or "(unnamed)", published, filename))
        print(f"{workflow['name']:32} {workflow['versionId']}  {published}")

    with open(VERSION_LOG, "a", encoding="utf-8", newline="\n") as f:
        if f.tell() == 0:
            f.write("# n8n workflow versions\n\nOne entry per `n8n_backup.py` run, newest last. "
                    "Snapshots live in `n8n_backups/` (local only).\n")
        f.write(f"\n## {stamp} — {label}\n\nSnapshot: `n8n_backups/{os.path.basename(snapshot_dir)}/`\n\n"
                "| Workflow | Workflow ID | Draft version ID | Version name | Published | File |\n"
                "|---|---|---|---|---|---|\n")
        for row in rows:
            name, wid, vid, vname, published, filename = row
            f.write(f"| {name} | `{wid}` | `{vid}` | {vname} | {published} | {filename} |\n")

    print(f"\n{len(rows)} workflows saved to {snapshot_dir}")


if __name__ == "__main__":
    main()
