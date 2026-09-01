"""Feed each generated feedback prompt to Gemini and assemble the results.

Reads manifest.json (written by make_feedback_prompts.py), sends every
"problem ### run ### feedback prompt.txt" to the Gemini API, and caches each
response as "problem ### run ### feedback result.txt" in the results folder --
so rerunning only calls the API for runs that don't have a result yet.

Then it assembles everything, plus the per-problem "problem ### user notes.txt"
files, into feedback_analysis_prompt.txt for the pass-2 systemic analysis.

Each call's token usage and cost are written back into manifest.json alongside
the run, and the run total (plus a to-date total across every priced run) is
printed at the end.

Needs a Gemini API key, unless --assemble-only. It is read from
google_api_key.txt (gitignored; override with --key-file), falling back to the
GOOGLE_API_KEY / GEMINI_API_KEY environment variables.

Standard library only -- no virtualenv needed.

Run from the base project folder.
"""

import argparse
import datetime as dt
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
KEY_FILE = Path("google_api_key.txt")
DEFAULT_MODEL = "gemini-3.7-flash"

# Gemini 3.7 Flash pricing, $ per 1M tokens. Promotional rates run through
# 2026-12-31 and exactly double on 2027-01-01; the rate set is chosen at runtime
# so this keeps costing correctly after the rollover. These are 3.7-flash rates
# only -- costing another model means adding its rates here.
PRICE_HIKE = dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)
PRICING_PROMO    = {"input": 0.75, "output": 3.75, "cache_read": 0.075}
PRICING_STANDARD = {"input": 1.50, "output": 7.50, "cache_read": 0.15}

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
# API key
# --------------------------------------------------------------------------

def read_key_file(path):
    """First non-blank, non-comment line of the key file, or None.

    Accepts a bare key or a GOOGLE_API_KEY=... / GEMINI_API_KEY=... line, so a
    file copied from a .env still works. Surrounding quotes are stripped.
    """
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line and line.split("=", 1)[0].strip().upper() in (
                "GOOGLE_API_KEY", "GEMINI_API_KEY"):
            line = line.split("=", 1)[1].strip()
        return line.strip("'\"") or None
    return None


def resolve_api_key(key_file):
    """Key from the key file, else the environment. Returns (key, source)."""
    key = read_key_file(key_file)
    if key:
        return key, str(key_file)
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(var):
            return os.environ[var], f"${var}"
    return None, None


# --------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------

def current_rates():
    return (PRICING_PROMO if dt.datetime.now(dt.timezone.utc) < PRICE_HIKE
            else PRICING_STANDARD)


def price_usage(usage, model):
    """Cost in USD for one call, from the response's usageMetadata.

    promptTokenCount already includes cachedContentTokenCount, so cached tokens
    are subtracted out and re-added at the cheaper cache-read rate. Thinking
    tokens are billed as output but are NOT included in candidatesTokenCount.
    """
    if model != DEFAULT_MODEL:
        return None, "priced rates are for %s only" % DEFAULT_MODEL

    rates = current_rates()
    prompt_tokens = usage.get("promptTokenCount", 0)
    cached = usage.get("cachedContentTokenCount", 0)
    output_tokens = (usage.get("candidatesTokenCount", 0)
                     + usage.get("thoughtsTokenCount", 0))
    fresh_input = max(prompt_tokens - cached, 0)

    cost = (fresh_input / 1_000_000) * rates["input"] \
         + (cached / 1_000_000) * rates["cache_read"] \
         + (output_tokens / 1_000_000) * rates["output"]
    return round(cost, 6), None


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
            text = "\n".join(p["text"] for p in parts if "text" in p)
            return text, data.get("usageMetadata", {})
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
        for run_num, entry in sorted(by_problem[prob_num], key=lambda t: t[0]):
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
    ap.add_argument("--key-file", default=str(KEY_FILE),
                    help="file holding the Gemini API key (default: %(default)s)")
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

    def save_manifest():
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    results_dir.mkdir(parents=True, exist_ok=True)

    api_key, key_source = resolve_api_key(Path(args.key_file))
    if not args.assemble_only:
        if not api_key:
            raise SystemExit(
                f"No API key found. Paste your Gemini key into {args.key_file} "
                "(one line, no quotes needed -- that file is gitignored), or set "
                "GOOGLE_API_KEY in the environment, or use --assemble-only.")
        print(f"using API key from {key_source}")

    called = cached = failed = 0
    run_cost = 0.0
    if not args.assemble_only:
        todo = []
        for prob_num in sorted(by_problem):
            for run_num, entry in sorted(by_problem[prob_num], key=lambda t: t[0]):
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
                result, usage = call_gemini(
                    prompt_path.read_text(encoding="utf-8"),
                    args.model, api_key)
            except RuntimeError as exc:
                print(f"  FAILED: {exc}", file=sys.stderr)
                failed += 1
                continue
            result_path.write_text(result, encoding="utf-8")
            called += 1

            cost, why_not = price_usage(usage, args.model)
            entry["model"] = args.model
            entry["input_tokens"] = usage.get("promptTokenCount", 0)
            entry["cached_tokens"] = usage.get("cachedContentTokenCount", 0)
            entry["output_tokens"] = (usage.get("candidatesTokenCount", 0)
                                      + usage.get("thoughtsTokenCount", 0))
            entry["cost_usd"] = cost
            if cost is None:
                print(f"  {entry['input_tokens']:,} in / "
                      f"{entry['output_tokens']:,} out -- not priced "
                      f"({why_not})")
            else:
                run_cost += cost
                print(f"  {entry['input_tokens']:,} in / "
                      f"{entry['output_tokens']:,} out -- ${cost:.4f}")
            # Written after every call so an interrupted run keeps its costs.
            save_manifest()

        rates = current_rates()
        promo = rates is PRICING_PROMO
        print(f"\n{called} calls made, {cached} already cached, {failed} failed.")
        print(f"This run: ${run_cost:.4f} at {args.model} "
              f"{'promotional' if promo else 'standard'} rates "
              f"(${rates['input']}/1M in, ${rates['output']}/1M out).")
        if promo:
            print("  Heads up: these rates double on 2027-01-01.")

    total = sum(e["cost_usd"] for e in manifest["runs"].values()
                if e.get("cost_usd"))
    priced = sum(1 for e in manifest["runs"].values() if e.get("cost_usd"))
    unpriced = len(manifest["runs"]) - priced
    print(f"All priced runs to date: ${total:.4f} across {priced} runs"
          + (f" ({unpriced} without a recorded cost)." if unpriced else "."))

    assemble(by_problem, prompts_dir, results_dir, Path(args.out))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
