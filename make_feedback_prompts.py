"""Generate one feedback-analysis prompt per row of the EDEE results spreadsheet.

Rows are grouped by Prompt (column A): each unique prompt text gets the next
problem number (persisted in manifest.json so numbering is stable across
reruns), and each row for that problem gets the next run number. For every
data row this writes
    notes/feedback/prompts_per_problem/problem ### run ### feedback prompt.txt
containing the full config.js agent definitions, the task history JSON that
column M points at, and the human feedback from columns P-S. It also creates
an empty "problem ### user notes.txt" per problem for the user to fill in.

Existing prompt files are never overwritten; the script complains about them
instead. manifest.json is merged/updated in place and later consumed by
run_feedback_analysis.py.

Input modes (in order of preference):
    --csv <file>          a CSV downloaded from the sheet (File > Download > CSV)
    --credentials <json>  a Google service-account key, read live via gspread
    (default)             the sheet's public CSV export URL, which only works
                          if the sheet is shared with "anyone with the link"

Run from the base project folder.
"""

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SHEET_ID = "1wL6uo_mwN3zWBuhk740ynfWeNTY37N2u1x4Ir-wlUkQ"
SHEET_NAME = "Sheet1"
SHEET_GID = "0"

CONFIG_JS = Path("config.js")
OUT_DIR = Path("notes") / "feedback" / "prompts_per_problem"
MANIFEST_NAME = "manifest.json"

# Column letter -> expected header, for the fields the scripts use.
COLUMNS = {
    "A": "Prompt",
    "E": "Job ID",
    "M": "History",
    "P": "Scaffolding Usable?",
    "Q": "Illustration Usable?",
    "R": "Other Notes",
    "S": "Fix",
}

PROMPT_TEMPLATE = """**AGENT DEFINITIONS**
{config_js}


**TASK HISTORY**
{task_history}


**USER FEEDBACK**
Scaffolding Usable?: {scaffolding_usable}
Illustration Usable?: {illustration_usable}
Designer notes: {other_notes}
General Problem Class: {fix}

Summarize and Analyze:
- what went wrong
- which agents were involved
- root causes in agent instructions
- SUGGESTIONS for improvement. Someone needs to look over hundreds of these to identify trends; what mitigates one issue might exacerbate another.
"""


def col_index(letter):
    """'A' -> 0, 'M' -> 12, 'AA' -> 26."""
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def cell(row, letter):
    idx = col_index(letter)
    return row[idx].strip() if idx < len(row) else ""


# --------------------------------------------------------------------------
# Sheet loading
# --------------------------------------------------------------------------

def rows_from_csv_file(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def rows_from_public_export():
    url = (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
           f"/export?format=csv&gid={SHEET_GID}")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = resp.read().decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"Could not read the sheet's public CSV export (HTTP {exc.code}).\n"
            "The sheet is not shared publicly. Either:\n"
            "  * download it (File > Download > Comma-separated values) and "
            "rerun with --csv <file>, or\n"
            "  * rerun with --credentials <service-account.json> after sharing "
            "the sheet with that service account's email."
        ) from exc
    if body.lstrip().startswith("<"):
        raise SystemExit(
            "The sheet's export URL returned an HTML login page instead of CSV; "
            "the sheet is not publicly readable. Use --csv or --credentials."
        )
    return list(csv.reader(io.StringIO(body)))


def rows_from_gspread(credentials_path):
    try:
        import gspread
    except ImportError:
        raise SystemExit("--credentials needs gspread: pip install gspread")
    client = gspread.service_account(filename=credentials_path)
    return client.open_by_key(SHEET_ID).worksheet(SHEET_NAME).get_all_values()


# --------------------------------------------------------------------------
# History fetching
# --------------------------------------------------------------------------

def fetch_history(url, cache):
    """Returns (text, ok). A failed fetch must not be baked into a prompt file:
    prompt files are never overwritten, so a transient network blip would
    otherwise poison that row permanently. The caller skips writing instead,
    leaving the row to be picked up by the next run."""
    if url in cache:
        return cache[url]
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            result = (resp.read().decode("utf-8", errors="replace"), True)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"  history fetch failed ({exc}): {url}", file=sys.stderr)
        result = (None, False)
    cache[url] = result
    return result


# --------------------------------------------------------------------------

def check_headers(header):
    for letter, expected in COLUMNS.items():
        actual = cell(header, letter)
        if actual.lower() != expected.lower():
            print(f"warning: column {letter} is '{actual}', expected "
                  f"'{expected}' -- the sheet layout may have changed.",
                  file=sys.stderr)


def load_manifest(path):
    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"problems": {}, "runs": {}}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--csv", help="local CSV export of Sheet1")
    src.add_argument("--credentials", help="Google service-account JSON key")
    ap.add_argument("--config", default=str(CONFIG_JS),
                    help="path to config.js (default: %(default)s)")
    ap.add_argument("--out-dir", default=str(OUT_DIR),
                    help="output folder (default: %(default)s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written without writing files, "
                         "downloading histories, or updating the manifest")
    args = ap.parse_args()

    config_path = Path(args.config)
    if not config_path.is_file():
        raise SystemExit(f"{config_path} not found -- run from the base project folder.")
    config_js = config_path.read_text(encoding="utf-8")

    if args.csv:
        rows = rows_from_csv_file(args.csv)
    elif args.credentials:
        rows = rows_from_gspread(args.credentials)
    else:
        rows = rows_from_public_export()

    if not rows:
        raise SystemExit("The sheet is empty.")
    check_headers(rows[0])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    problems = manifest["problems"]   # prompt text -> problem number
    runs = manifest["runs"]           # job key -> {problem, run, ...}
    runs_per_problem = {}
    for entry in runs.values():
        p = entry["problem"]
        runs_per_problem[p] = max(runs_per_problem.get(p, 0), entry["run"])

    history_cache = {}
    written = existing = skipped = fetch_failed = 0

    for offset, row in enumerate(rows[1:], start=1):
        if not any(c.strip() for c in row):
            skipped += 1
            continue

        prompt_text = cell(row, "A")
        if prompt_text not in problems:
            problems[prompt_text] = max(problems.values(), default=0) + 1
        prob_num = problems[prompt_text]

        # Job ID is the stable identity of a run; fall back to the row number.
        job_key = cell(row, "E") or f"row-{offset}"
        if job_key in runs:
            run_num = runs[job_key]["run"]
            if runs[job_key]["problem"] != prob_num:
                print(f"warning: job {job_key} moved from problem "
                      f"{runs[job_key]['problem']} to {prob_num}; keeping the "
                      "original assignment.", file=sys.stderr)
                prob_num = runs[job_key]["problem"]
        else:
            run_num = runs_per_problem.get(prob_num, 0) + 1
            runs_per_problem[prob_num] = run_num

        name = f"problem {prob_num:03d} run {run_num:03d} feedback prompt.txt"
        path = out_dir / name

        # Refresh manifest feedback fields even when the prompt file exists, so
        # later sheet edits to P-S reach the assembled analysis file. Merged, not
        # replaced: run_feedback_analysis.py writes token/cost keys into these
        # same entries and they must survive a regeneration.
        runs.setdefault(job_key, {}).update({
            "problem": prob_num,
            "run": run_num,
            "prompt_file": name,
            "scaffolding_usable": cell(row, "P"),
            "illustration_usable": cell(row, "Q"),
            "other_notes": cell(row, "R"),
            "fix": cell(row, "S"),
        })

        notes_path = out_dir / f"problem {prob_num:03d} user notes.txt"
        if not args.dry_run and not notes_path.exists():
            notes_path.write_text("", encoding="utf-8")

        if path.exists():
            print(f"already exists, not overwriting: {path}", file=sys.stderr)
            existing += 1
            continue

        history_url = cell(row, "M")
        if args.dry_run:
            print(f"would write {path} (history: {history_url or 'none'})")
            written += 1
            continue

        if history_url:
            task_history, ok = fetch_history(history_url, history_cache)
            if not ok:
                print(f"  skipping problem {prob_num} run {run_num} -- rerun to "
                      "retry it", file=sys.stderr)
                fetch_failed += 1
                continue
        else:
            print(f"warning: no history URL for problem {prob_num} run "
                  f"{run_num}; its prompt will have no task history to analyze.",
                  file=sys.stderr)
            task_history = "[no history URL in column M for this row]"

        path.write_text(PROMPT_TEMPLATE.format(
            config_js=config_js,
            task_history=task_history,
            scaffolding_usable=cell(row, "P"),
            illustration_usable=cell(row, "Q"),
            other_notes=cell(row, "R"),
            fix=cell(row, "S"),
        ), encoding="utf-8")
        print(f"wrote {path}")
        written += 1

    if not args.dry_run:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n{written} written, {existing} already existed, "
          f"{skipped} blank rows skipped, {len(problems)} unique problems.")
    if fetch_failed:
        print(f"{fetch_failed} row(s) skipped because their history download "
              "failed -- rerun to retry just those.", file=sys.stderr)
    return 1 if (existing or fetch_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
