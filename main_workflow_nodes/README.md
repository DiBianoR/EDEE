# n8n changes to make — human-in-the-loop scaffolding review

The workflow JSON exports in this repo are read-only mirrors; apply these changes in
the n8n editor. Everything outside n8n (config.js, the three services) is already
done in this repo and only needs deploying.

Contents

1. [What you're building](#1-what-youre-building)
2. [Prerequisites (deploy first)](#2-prerequisites-deploy-first)
3. [Main Workflow: node-by-node](#3-main-workflow-node-by-node)
4. [Wiring](#4-wiring)
5. [Smaller edits to existing nodes](#5-smaller-edits-to-existing-nodes)
6. [Verification checklist](#6-verification-checklist)
7. [How the pieces talk to each other](#7-how-the-pieces-talk-to-each-other)

---

## 1. What you're building

After the machine inspection of the scaffolding (`inspections passed?`), the run can
pause and let the person who launched it from the frontend look at the diagram:

| `human_review_mode` (from the frontend sidebar) | inspectors PASS the scaffold | inspectors REJECT it 3× (MaxRetriesExceeded) |
|---|---|---|
| `automatic` (default) | continue, as today | fail, as today |
| `timeout` | pause **30 s**; if nobody clicks *Corrections*, continue | pause up to **10 min** for the human; nobody → fail |
| `wait` | pause up to **10 min** for the human; nobody → continue | pause up to **10 min**; nobody → fail |

At the gate the human can **Continue** (accept as drawn), **Give up** (clean failure via
`report_error`), or **give corrections**, which opens a multi-turn conversation with
`inspection_manager` (tasks `human_review_open` / `human_review_reply`, prompt author
`user`). The manager asks until it is confident it understands, then sets
`understanding_confirmed`; the pipeline re-runs the coding loop from `cfg18` with **all
retry counters reset**, `retry_directives` = the human variant, and
`{human_priority_directives}` armed for every later `consolidate_inspection`. Every
message the human sends re-enters that path, so retries reset on each suggestion. If the
human walks away mid-conversation for 10 min, the run proceeds with whatever
corrections the manager already holds. After the redraw passes inspection the gate
opens again (so the human can check the fix); *Continue* / *Give up* are always
available, which is what prevents infinite loops.

New pieces, all in Phase 3 of the Main Workflow, between `inspections passed?` and
`Archive Scaffolding`:

```
                                       ┌─────────────────────┐        false
 inspections passed? ──[True]─────────►│   Human review?     ├──────────────────────────────► Archive Scaffolding
                                       └──────────┬──────────┘                                       ▲
                                                  │ true                                             │
 inspections passed? ──[MaxRetries]───►┌──────────┴──────────┐  false                                │
                                       │  Human available?   ├────────────► cfg53 (report_error)    │
                                       └──────────┬──────────┘                    ▲                  │
                                                  │ true                          │                  │
             ┌────────────────────────────────────▼────────┐                      │                  │
             │  Human Gate: Announce  (Code)                │◄──────────┐          │                  │
             └───────────────────────┬─────────────────────┘           │          │                  │
                                     ▼                                 │          │                  │
             ┌───────────────────────────────────────────────┐         │          │                  │
             │  Human Gate: Wait  (Wait · resume on webhook, │         │          │                  │
             │  limit = {{$json.human_wait_seconds}} s)      │         │          │                  │
             └───────────────────────┬─────────────────────┘           │          │                  │
                                     ▼                                 │          │                  │
             ┌───────────────────────────────────────────────┐         │          │                  │
             │  Human Gate: Resolve  (Code)                  │         │          │                  │
             └───────────────────────┬─────────────────────┘           │          │                  │
                                     ▼                                 │          │                  │
             ┌───────────────────────────────────────────────┐ corrections        │                  │
             │  Human decision  (Switch on human_action)     ├──────────┘          │                  │
             └──┬───────────────┬──────────────────────────┘                      │                  │
        message │               │ continue · rework · fail · abort                │                  │
                ▼               ▼                                                 │                  │
   cfg human review        ┌───────────────┐                                      │                  │
        │                  │ Human outcome │◄────────────────┐                    │                  │
        ▼                  │    (Code)     │                 │ true               │                  │
   inspection_manager -    └──────┬────────┘                 │                    │                  │
   human_review (UA)              ▼                 ┌────────┴──────────┐         │                  │
        │                  cfg log human decision   │ Manager understood?│◄───────┘ (from UA)        │
        ▼                         ▼                 └────────┬──────────┘                            │
   Manager understood? ──false──► Human Gate: Announce       │                                       │
                           human_gate - log_human_decision (UA)                                      │
                                  ▼                                                                  │
                           ┌──────────────────────────────────────────────┐   continue               │
                           │ After human log (Switch on                   ├──────────────────────────┘
                           │ $('Human outcome').item.json.human_action)   ├── fail ───────► cfg53
                           └──────────────┬──────────────────┬────────────┘
                                  rework  │                   │ abort
                                          ▼                   ▼
                     Human corrections → reset retries    cfg human abort ──► error_handler - report_error7
                                          │
                                          ▼
                                        cfg18  (plan_logic — the normal coding retry loop)
```

## 2. Prerequisites (deploy first)

1. **`config.js` → Load Config sub-workflow.** Paste the repo's `config.js` into the
   `Load Config` Code node. It adds agents `human_gate`, tasks `human_review_open`,
   `human_review_reply`, `log_human_decision`, `config.human_review_mode`,
   `config.human_review_timeouts`, two new `retry_directive_library` entries
   (`plan_logic_human`, `inspection_human`), seeds `human_priority_directives: ""`, and
   updates `EXECUTION_CONTRACT` / `write_code` for the new render service.
   ⚠️ The live node currently differs from the repo in one line —
   `"provider_by_type": { ..., img2img: "openai" }` — the repo says `google`. Re-apply
   whichever you want after pasting.
2. **`ritel-state-manager`** — redeploy. Two new endpoints: `POST /human-decision/{job_id}`
   (relays a decision to the paused execution) and `POST /human-activity/{job_id}` (the
   typing beacon that extends an open gate's deadline). Also wholesale replacement of
   `human_review` on `/update-state`; `requests` added to requirements.
   ⚠️ **Until this is redeployed, typing detection does not work** — `/human-activity`
   404s, the gate keeps its plain 30-second deadline, and typing into the box will not
   hold it open. The frontend now says so in the countdown line rather than leaving you
   to guess, but the fix is the redeploy.
3. **`render-matplotlib`** — redeploy (single-namespace exec, `keep_axes` / `square` /
   `dpi` request fields, sandbox import guard, line-numbered errors, "drew nothing" error).
4. **`ritel-frontend`** — redeploy (Python 3.12 image, Streamlit ≥ 1.50; sends
   `human_review_mode`, `max_text_tier`, `max_image_tier`; review-gate UI).

## 3. Main Workflow: node-by-node

Node names matter: the Code nodes reach back by name (`Human Gate: Announce`,
`Human outcome`, `Prepare for Vision`). Keep them exactly as written.

### 3.1 `Human review?` — IF (v2.2)

* Condition (boolean, is true): `={{ $json.config.human_review_mode !== 'automatic' }}`
* true → `Human Gate: Announce` · false → `Archive Scaffolding`

### 3.2 `Human available?` — IF (v2.2)

* Same condition as 3.1.
* true → `Human Gate: Announce` · false → `cfg53` (the existing MaxRetriesExceeded path)

### 3.3 `Human Gate: Announce` — Code (JavaScript, Run Once for All Items)

* Paste [`human_gate_announce.js`](human_gate_announce.js).
* Reads `config.human_review_mode` / `human_review_timeouts`, decides stage
  (`gate` / `conversation`) and reason (`review` / `max_retries`), POSTs
  `status: "awaiting_human"` + `human_review{…, resume_url: $execution.resumeUrl}` to the
  listener, and outputs `human_wait_seconds` for the Wait node.
* It sends **no image**: `Prepare for Vision` already broadcast this render, so
  `{job_id}/latest.png` is current and the frontend is showing it. That makes the
  broadcast load-bearing — see §5.2.

### 3.4 `Human Gate: Wait` — Wait (v1.1)

| Parameter | Value |
|---|---|
| Resume | **On Webhook Call** |
| HTTP Method | POST |
| Respond | Immediately |
| Limit Wait Time | **on** |
| Limit Type | After Time Interval |
| Amount | `={{ $json.human_wait_seconds }}` |
| Unit | Seconds |
| Webhook Suffix | *(leave empty — Announce publishes `$execution.resumeUrl` as-is)* |

On webhook resume the node outputs the request (`{headers, params, query, body}`);
on timeout it passes the Announce item through. `Human Gate: Resolve` handles both.

⚠️ `human_wait_seconds` is **one slice, not the whole budget.** A Wait node's limit is
fixed when it starts, so a running wait can never be extended. Activity detection
therefore works by letting a short slice lapse and going round again: see §3.6's
`extend` output.

### 3.5 `Human Gate: Resolve` — Code (Run Once for All Items)

* Paste [`human_gate_resolve.js`](human_gate_resolve.js).
* Outputs `human_action` ∈ `continue | message | corrections | rework | fail | abort`,
  `human_message`, `human_decision_text`, and the echoed stage/reason/round.

### 3.6 `Human decision` — Switch (v3, rules mode)

Seven rules, all `={{ $json.human_action }}` *string equals*, each renamed output:

| Output key | Goes to | |
|---|---|---|
| `message` | `cfg human review` | the human typed something |
| `continue` | `Human outcome` | empty box, or a timeout |
| `extend` | `Human Gate: Announce` | activity loop |
| `rework` | `Human outcome` | |
| `fail` | `Human outcome` | |
| `abort` | `Human outcome` | now only from the manager's verdict |
| `corrections` | `Human Gate: Announce` | **dead** — keep or delete, see below |

The review UI has one box and one button, so it only ever sends `message` (box had text)
or `continue` (box empty). `corrections` can no longer be emitted and `abort` no longer
arrives from a click — it comes from `Human outcome` reading the manager's verdict.
Leaving both rules in place is harmless; they simply never match. Delete them only if you
also remove their connections.

`extend` is the activity loop. When a slice lapses, Resolve asks the state manager for
the gate's current deadline; `POST /human-activity` has been pushing that deadline out
on every keystroke, so a deadline still in the future means the human is mid-thought and
we open another slice instead of deciding for them. The effect is that the 30-second
window in `timeout` mode is 30 seconds **of silence**, and every other wait is "ten
minutes since the last sign of life" rather than ten minutes flat. It terminates on its
own: the deadline only moves while someone is actually typing, and the state manager
caps a single gate at `MAX_GATE_MINUTES` (30) regardless.

### 3.7 `cfg human review` — Set (Include Other Fields: on)

| Field | Type | Value |
|---|---|---|
| `PHASE_ID` | string | `3` |
| `AGENT_ID` | string | `inspection_manager` |
| `TASK_ID` | string | `={{ $json.session_events.filter(e => e.author === 'inspection_manager' && String(e.task).startsWith('human_review')).length > 0 ? 'human_review_reply' : 'human_review_open' }}` |
| `prompt_author` | string | `user` |
| `human_message` | string | `={{ $json.human_message }}` |

(`prompt_author: "user"` is what makes the human's words log as `[user]` events and
replay as real user turns. `human_message` is hoisted by the task so it never settles
into session_state.)

**No `base64_img_string` here, deliberately.** Unlike every other stage-3 cfg node, this
one attaches no image. The *human* is the one looking at the render, in the frontend,
from `{job_id}/latest.png`; the manager works from the blueprint, the inspectors'
reports and the generating code, which are already in its scope. Attaching a copy would
add a full image payload **per conversation round** to a workflow that already carries
the render through 16 nodes and runs out of memory on long executions. The two tasks
therefore have no `model_type: "view_img"` override either — adding the image back
means restoring both, or Node 1 will prefix `[warning: no image attached to this vision
request]` to the prompt.

### 3.8 `inspection_manager - human_review` — Execute Workflow

Same as every other agent call: Universal Agent Sub-Workflow, passthrough input.

### 3.9 `Manager understood?` — IF (v2.2)

* Condition (boolean, is true): `={{ $json.session_state.understanding_confirmed === true }}`
* true → `Human outcome` · false → `Human Gate: Announce`
  (Announce sees the last event is a `human_review_*` reply and opens a *conversation*
  wait, showing `reply_to_human` to the human.)

### 3.10 `Human outcome` — Code (Run Once for All Items)

* Paste [`human_outcome.js`](human_outcome.js).
* Funnel for `continue | rework | fail | abort`. On the manager path it now reads three
  flags in this order: `user_wants_to_stop` → abort, else `scaffold_acceptable_as_is` →
  continue, else rework. **This is the only node that changes for the single-button
  review**, and it is the node that replaces the old "Give up" click.

### 3.11 `cfg log human decision` — Set (Include Other Fields: on)

| Field | Value |
|---|---|
| `PHASE_ID` | `3` |
| `AGENT_ID` | `human_gate` |
| `TASK_ID` | `log_human_decision` |

`human_decision_text` is already on the item (from `Human outcome`) — the no_model task
templates it and hoists it back out of state.

### 3.12 `human_gate - log_human_decision` — Execute Workflow (Universal Agent)

### 3.13 `After human log` — Switch (v3, rules mode)

Rules on `={{ $('Human outcome').item.json.human_action }}` (string equals):

| Output key | Goes to |
|---|---|
| `continue` | `Archive Scaffolding` |
| `rework` | `Human corrections → reset retries` |
| `fail` | `cfg53` |
| `abort` | `cfg human abort` |

### 3.14 `Human corrections → reset retries` — Set (Include Other Fields: on)

| Field | Type | Value |
|---|---|---|
| `coding_retry_count` | number | `0` |
| `execution_retry_count` | number | `0` |
| `inspection_retry_count` | number | `0` |
| `retry_directives` | string | `={{ $json.config.retry_directive_library.plan_logic_human }}` |
| `human_priority_directives` | string | `={{ $json.config.retry_directive_library.inspection_human }}` |

→ `cfg18`. cfg18 already does `coding_retry_count + 1` and picks up `retry_directives`
from the item; `human_priority_directives` rides top-level into the next Universal Agent
call, where Node 1 sweeps it into `session_state`, so every later
`consolidate_inspection` sees the `[HUMAN IN THE LOOP]` block.

### 3.15 `cfg human abort` — Set (Include Other Fields: on)

| Field | Value |
|---|---|
| `PHASE_ID` | `-` |
| `AGENT_ID` | `error_handler` |
| `TASK_ID` | `report_error` |
| `error` | `UserAborted` |

### 3.16 `error_handler - report_error7` — Execute Workflow (Universal Agent)

Terminal (`report_error` has `terminal_mode: failed`), like the other `report_error*`
nodes. The history already holds the `human_gate` note "chose to stop the run", so the
explanation to the user is accurate.

## 4. Wiring

Remove: `inspections passed? [True] → Archive Scaffolding` and
`inspections passed? [MaxRetriesExceeded] → cfg53`. Add:

```
inspections passed? [True]               → Human review?
inspections passed? [MaxRetriesExceeded] → Human available?
Human review?     [true]  → Human Gate: Announce      [false] → Archive Scaffolding
Human available?  [true]  → Human Gate: Announce      [false] → cfg53
Human Gate: Announce → Human Gate: Wait → Human Gate: Resolve → Human decision
Human decision [message]     → cfg human review → inspection_manager - human_review → Manager understood?
Human decision [corrections] → Human Gate: Announce
Human decision [extend]      → Human Gate: Announce      (activity loop: another slice)
Human decision [continue|rework|fail|abort] → Human outcome
Manager understood? [true] → Human outcome            [false] → Human Gate: Announce
Human outcome → cfg log human decision → human_gate - log_human_decision → After human log
After human log [continue] → Archive Scaffolding
After human log [rework]   → Human corrections → reset retries → cfg18
After human log [fail]     → cfg53
After human log [abort]    → cfg human abort → error_handler - report_error7
```

`Archive Scaffolding` now has three inbound edges (Route Workflow3 DIRECT_IMAGE_GEN,
`Human review?` false, `After human log` continue); it uses `$input`, so nothing
inside it changes.

## 5. Smaller edits to existing nodes

1. **`Set Job`** — add assignment `human_review_mode` (string):
   `={{ $json.body?.human_review_mode || $json?.human_review_mode || "automatic" }}`.
   (The Test Orchestrator sends nothing → `automatic`, so batch runs never pause.)
2. **`Prepare for Vision`** — raise the GUI broadcast timeout from `1500` to `15000` ms.
   That POST carries the full render as base64 and is the **only** thing that puts the
   scaffolding in `{job_id}/latest.png`, which is what the human reviews. At 1.5 s it can
   silently time out on a large image (the catch block swallows it), and the gate would
   then open on a stale or missing picture. It stays fire-and-forget — a lost progress
   ping must not kill a run — but it needs room to actually land. The other image-bearing
   broadcasts already use 10–30 s.
3. **`Python Execution Node`** — add body parameter `keep_axes` =
   `={{ $json.session_state.needs_graph_planning === true }}`. Graph problems now keep
   their axes/ticks/grid; everything else is stripped as before. Optional extras the
   service accepts: `square` (bool), `dpi` (50–300), `transparent` (bool).
4. **`cfg57`** (execution-error injector) — optional robustness: n8n sometimes puts the
   HTTP response body in `error.description` rather than `error.message`. Add
   `else if (inputData.error.description) { errorText = inputData.error.description; }`
   before the `response.data` branch. The render service's error text now includes
   `line N: <source>` frames, which reach `review_manager.troubleshoot` through here.
5. **Universal Agent Sub-Workflow** — no changes. `prompt_author` is already a
   recognised top-level input of Node 1.
6. **Global Error Handler** — no changes.

## 6. Verification checklist

* [ ] Load Config: run the Main Workflow manually (`Mock Input1`, query 3) with
      `human_review_mode` absent → run completes exactly as before (no gate).
* [ ] Frontend, mode *Ask me, 30 s to react*: after the inspectors pass, the frontend shows
      the gate with a 0:30 countdown and the scaffold; do nothing → run continues and the
      transcript has a `human_gate` note "did not respond within 30s".
* [ ] Same mode, but start typing in the corrections box before the 30 s elapses: the
      countdown jumps to ~10:00, the note "Typing keeps this topped up" appears, the gate
      survives well past 30 s, and the half-typed text is **not** cleared when the
      deadline moves. (That last part is what `opened_at` keying protects — a regression
      here wipes the message mid-sentence.) Watch for `extend` firing in the n8n
      execution: one `Human Gate: Announce` run per slice.
* [ ] Block the beacon (dev tools offline, or a bad `STATE_MANAGER_URL`): the gate falls
      back to the plain 30 s / 10 min deadline rather than hanging or erroring.
* [ ] Mode *Wait for me*: click **Corrections**, type a change, **Send** → a `[user]`
      event and an `inspection_manager · human_review_open` reply appear in the log; answer
      its question → `understanding_confirmed` → coding loop re-runs with counters at 0
      (`cfg18` shows `coding_retry_count: 1`) → new scaffold → gate opens again →
      **Continue** → artist.
* [ ] Mode *Wait for me*, submit an empty box → the run continues immediately with no
      model call, and the transcript records "accepted the scaffolding as drawn".
* [ ] Mode *Wait for me*, type something that unmistakably asks to stop ("forget it,
      cancel this run") → the manager sets `user_wants_to_stop`, `Human outcome` emits
      `abort`, and the run ends as a clean failure explaining that you stopped it.
* [ ] Type a blunt criticism that is NOT a request to stop ("this is useless, the labels
      are unreadable") → it must be treated as a correction and redraw, not an abort.
      This is the judgement the old Give up button used to make for you, so it is worth
      checking on your own prompts and model tier.
* [ ] Force MaxRetriesExceeded (temporarily set the threshold in `inspections passed?` to
      `>= 1`) with a non-automatic mode → gate opens with reason `max_retries`; **Accept
      as is** → Archive Scaffolding → Phase 4.
* [ ] While the gate is open, `GET /summary/{job_id}` shows `status: awaiting_human` and a
      `human_review` block; after any decision it is `{ stage: "closed", … }` — never a
      stale `resume_url`.

## 7. How the pieces talk to each other

```
frontend ──(webhook: human_review_mode)──► Set Job ─► Load Config (config.human_review_mode)
n8n Announce ──POST /update-state {status: awaiting_human, human_review{resume_url,…}}──► ritel-state-manager ─► Firestore
frontend ◄──poll Firestore doc──  shows gate / countdown / conversation
frontend ──POST /human-activity/{job_id} (throttled, on keydown/input/click)──► pushes human_review.deadline out
n8n slice lapses → Resolve reads that deadline → still in the future? → "extend" → Announce opens another slice
frontend ──POST /human-decision/{job_id} {action, message}──► ritel-state-manager ──POST resume_url──► n8n Wait resumes
n8n Resolve → (agents run, node 1/3 broadcast as usual; prompt author "user") → Announce again or Archive Scaffolding
```

The human's messages live in the transcript as ordinary events (`author: "user"`,
task `human_review_*`), so `session_events.json`, the ZIP, `final_reporter` and
`error_handler` all see them; `human_gate` events record every gate decision.
