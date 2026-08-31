"""Feed each generated feedback prompt to Gemini and assemble the results.

Reads manifest.json (written by make_feedback_prompts.py), sends every
"problem ### run ### feedback prompt.txt" to the Gemini API, and caches each
response as "problem ### run ### feedback result.txt" in the results folder --
so rerunning only calls the API for runs that don't have a result yet.

Then it assembles everything, plus the per-problem "problem ### user notes.txt"
files, into feedback_analysis_prompt.txt for the pass-2 systemic analysis.

Needs a Gemini API key in the GEMINI_API_KEY (or GOOGLE_API_KEY) environment
variable, unless --assemble-only.

Run from the base project folder.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROMPTS_DIR = Path("notes") / "feedback" / "prompts_per_problem"
RESULTS_DIR = Path("notes") / "feedback" / "results_per_problem"
OUT_FILE = Path("notes") / "feedback" / "feedback_analysis_prompt.txt"
DEFAULT_MODEL = "gemini-3.7-flash"

TRAILING_INSTRUCTIONS = """\
---------------------------

Look over everything and try to find systemic issues that come up over and over again, Create a succinct, easy to read summary of the common issues we need to fix, most widespread problems first.
- common problems with scaffolding
- common problems with final image
- what went wrong
- which agents were involved
- root causes in agent instructions
- suggestions for improvement.
  - Try to make general rules/observations over problem specific fixes where possible
  - We can and should make special rules for major problem classes though, educational math problems fall into a mostly finite and static number of categories.
  - The narrowest fix should be for an entire class of problems, never specific to this single problem.
"""


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

def call_gemini(prompt, model, api_key, retries=3):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
    }).encode("utf-8")

    last_err = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        })
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parts = data["candidates"][0]["content"]["parts"]
            return "\n".join(p["text"] for p in parts if "text" in p)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            last_err = f"HTTP {exc.code}: {detail}"
            if exc.code not in (429, 500, 503) or attempt == retries:
                raise RuntimeError(last_err) from exc
            wait = 15 * attempt
            print(f"  {last_err[:120]} -- retrying in {wait}s "
                  f"({attempt}/{retries})", file=sys.stderr)
            time.sleep(wait)
        except (urllib.error.URLError, OSError, KeyError, IndexError) as exc:
            last_err = repr(exc)
            if attempt == retries:
                raise RuntimeError(last_err) from exc
            time.sleep(15 * attempt)
    raise RuntimeError(last_err)


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def read_or(path, placeholder):
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return placeholder


def assemble(by_problem, prompts_dir, results_dir, out_file):
    problem_blocks = []
    for prob_num in sorted(by_problem):
        run_blocks = []
        for run_num, entry in sorted(by_problem[prob_num]):
            result_path = results_dir / (
                f"problem {prob_num:03d} run {run_num:03d} feedback result.txt")
            result = read_or(result_path,
                             f"[no Gemini result yet for problem {prob_num} "
                             f"run {run_num}]")
            run_blocks.append(
                f"--{run_num}--\n"
                f"Scaffolding Usable?: {entry['scaffolding_usable']}\n"
                f"Illustration Usable?: {entry['illustration_usable']}\n"
                f"Other notes: {entry['other_notes']}\n"
                f"General Problem Class: {entry['fix']}\n"
                f"\n{result}"
            )

        notes = read_or(prompts_dir / f"problem {prob_num:03d} user notes.txt",
                        "(none)")
        problem_blocks.append(
            f"**Problem {prob_num}**\n\n"
            + "\n\n".join(run_blocks)
            + f"\nProblem {prob_num} User Notes:\n{notes}"
        )

    out_file.write_text(
        "\n\n\n".join(problem_blocks) + "\n\n\n" + TRAILING_INSTRUCTIONS,
        encoding="utf-8")
    print(f"assembled {out_file} ({len(problem_blocks)} problems)")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompts-dir", default=str(PROMPTS_DIR),
                    help="folder with prompts + manifest.json (default: %(default)s)")
    ap.add_argument("--results-dir", default=str(RESULTS_DIR),
                    help="folder for cached Gemini results (default: %(default)s)")
    ap.add_argument("--out", default=str(OUT_FILE),
                    help="assembled analysis prompt (default: %(default)s; "
                         "regenerated every run)")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="Gemini model name (default: %(default)s)")
    ap.add_argument("--limit", type=int,
                    help="call the API for at most N missing results this run "
                         "(good for a cheap first test)")
    ap.add_argument("--assemble-only", action="store_true",
                    help="skip all API calls; assemble from existing results")
    args = ap.parse_args()

    prompts_dir = Path(args.prompts_dir)
    results_dir = Path(args.results_dir)
    manifest_path = prompts_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"{manifest_path} not found -- run "
                         "make_feedback_prompts.py first.")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    by_problem = {}   # problem number -> [(run number, manifest entry), ...]
    for entry in manifest["runs"].values():
        by_problem.setdefault(entry["problem"], []).append((entry["run"], entry))

    results_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not args.assemble_only and not api_key:
        raise SystemExit("Set GEMINI_API_KEY (or GOOGLE_API_KEY), or use "
                         "--assemble-only.")

    called = cached = failed = 0
    if not args.assemble_only:
        todo = []
        for prob_num in sorted(by_problem):
            for run_num, entry in sorted(by_problem[prob_num]):
                result_path = results_dir / (
                    f"problem {prob_num:03d} run {run_num:03d} "
                    "feedback result.txt")
                if result_path.exists():
                    cached += 1
                else:
                    todo.append((prob_num, run_num, entry, result_path))

        if args.limit is not None:
            todo = todo[:args.limit]

        for i, (prob_num, run_num, entry, result_path) in enumerate(todo, 1):
            prompt_path = prompts_dir / entry["prompt_file"]
            if not prompt_path.is_file():
                print(f"missing prompt file, skipping: {prompt_path}",
                      file=sys.stderr)
                failed += 1
                continue
            print(f"[{i}/{len(todo)}] problem {prob_num} run {run_num} "
                  f"-> {args.model} ...")
            try:
                result = call_gemini(
                    prompt_path.read_text(encoding="utf-8"),
                    args.model, api_key)
            except RuntimeError as exc:
                print(f"  FAILED: {exc}", file=sys.stderr)
                failed += 1
                continue
            result_path.write_text(result, encoding="utf-8")
            called += 1

        print(f"\n{called} calls made, {cached} already cached, {failed} failed.")

    assemble(by_problem, prompts_dir, results_dir, Path(args.out))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
