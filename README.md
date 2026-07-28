# EDEE — Educational Diagram Engineering Engine

Fully automated generation of mathematically accurate, illustrated diagrams for math word problems.

Give EDEE a word problem ("A farmer's rectangular field is 12 m by 8 m...") and it returns a polished, textbook-quality illustration whose geometry — counts, dimensions, angles, arrangements, labels — is actually correct.

## How it works: two-pass generation

Image generation models are bad at math, and matplotlib is bad at art. EDEE uses both, in sequence:

1. **Scaffolding pass** — A team of LLM agents plans the diagram, designs a barebones geometric blueprint, writes Python/matplotlib code for it, and executes that code in a sandboxed cloud function. The result is a mathematically precise "skeleton" image.
2. **Artistic pass** — The skeleton is handed to an image-to-image model, which paints the final illustration *over* the scaffolding — preserving the underlying geometry while adding a chosen art style (storybook watercolor, casual mobile game, cel-shaded anime, claymation diorama, or mid-century modern).

## The agent pipeline

Roughly 25 specialized agents run across six stages, orchestrated by n8n. Every agent call goes through a single reusable **Universal Agent** sub-workflow, driven entirely by declarative config.

| Stage | Focus | Key agents |
|---|---|---|
| 1. Validation & Initial Planning | Extract the math problem and/or explicit visual request, check for conflicts, propose what to draw | `problem_validation`, `image_description` |
| 2. Description Refinement | Fan out to specialists, then merge their plans into one dense, rendering-ready visual description | `image_detail_planner` (manager), `dimension_expert`, `layout_expert`, `visual_director`, `markup_specialist`, `educator`, plus conditional `3d_specialist`, `data_viz_expert`, `arrangement_planner`, `artistic_planner` |
| 3. Base Diagram Generation | Choose a strategy (standard matplotlib / composite primitives / direct image gen), design the geometric blueprint, write and review code, render, and vision-QA the result | `selector`, `scaffolding_designer`, `architect`, `builder`, `reviewer`, `inspector` |
| 4. Advanced Image Generation | Convert the technical result into an artistic prompt and render the final illustration via img2img | `image_planner`, `artist` |
| 5. Review & Postprocessing | Audit the illustration for accuracy, safety, and bias; make the release decision | `image_verifier`, `issue_aggregator` |
| 6. Final Output & Report | Return the image with a user-facing summary and a developer archival report | `final_reporter` |

A separate **Global Error Handler** workflow catches crashes and unrecoverable loops anywhere in the pipeline, diagnoses the root cause from the full run history, and explains it to the user in plain language (`error_handler`, `error_expert`, `error_injector`).

Retry loops are built in at several checkpoints — description review, code syntax/logic checks, and visual inspection — with retry caps and a deliberately lenient second-pass inspector to prevent infinite loops.

## Architecture

```
Streamlit frontend ──► n8n cloud (Main Workflow)
     ▲                      │
     │                      ├─► Universal Agent Sub-Workflow ──► Gemini / OpenAI APIs
     │                      ├─► render-matplotlib (Cloud Function) ──► scaffolding PNG
     │                      └─► ritel-state-manager (Cloud Run)
     │                                │
     └────── Firestore (job state, logs) + GCS bucket (images, history)
```

- **n8n workflows** (`Main Workflow.json`, `Universal Agent Sub-Workflow.json`, `Load Config Sub-workflow.json`, `EDEE Global Error Handler.json`) — the orchestration layer. The Main Workflow wires the six stages together and routes between branches; the Universal Agent Sub-Workflow is a generic three-node agent runner shared by every task.
- **Universal Agent code nodes** (`universal_agent_1_of_3-Prepare_Payload.js`, `universal_agent_2_of_3.js`, `universal_agent_3_of_3-Parse_&_Snowball.js`) — the JS source for those three nodes:
  1. *Prepare Payload* — resolves the task's agent from config, templates `{variables}` from session state, filters conversation history by `history_scope`, and attaches a JSON schema for constrained output.
  2. *API Call* — provider switch (Google / OpenAI) with tiered models (`fast` / `medium` / `slow`) per task or agent.
  3. *Parse & Snowball* — parses the response and folds ("snowballs") results back into `session_state` and the event log for downstream agents.
- **`config.js`** — the single source of truth for the whole agent system: agent identities, task instructions, output schemas, history scopes, model tiers, the model registry, and the style library. Tasks and agents are flat registries (ADK-style contract); a task points at its agent via `assigned_agent`, and `history_scope` is an explicit list of event authors each agent is allowed to see.
- **`render-matplotlib/`** — a Google Cloud Function that executes the LLM-written matplotlib code and returns a PNG. It applies post-processing safeguards (strips axes/grids/ticks, enforces a strict 1:1 aspect ratio, adds margins, crops whitespace) so coder-agent mistakes can't ruin the render, and returns the raw Python traceback on failure so the reviewer agent can fix the code.
- **`ritel-state-manager/`** — a FastAPI service (Cloud Run) that receives progress updates from the pipeline and persists job state and agent thought logs to Firestore, plus images and run history to a GCS bucket.
- **`ritel-frontend/`** — a Streamlit app where a user submits a problem, watches the agents' live thought log while the job runs, and browses the results (scaffolding vs. final image carousel, downloadable job archive).

## Repository layout

```
config.js                              Agent/task/model/style configuration (source of truth)
Main Workflow.json                     n8n: six-stage pipeline orchestration
Universal Agent Sub-Workflow.json      n8n: reusable agent runner
Load Config Sub-workflow.json          n8n: loads config.js into the workflow
EDEE Global Error Handler.json         n8n: pipeline-wide error diagnosis
universal_agent_*.js                   Source for the Universal Agent's three code nodes
render-matplotlib/                     Cloud Function: sandboxed matplotlib rendering
ritel-state-manager/                   Cloud Run: job state + artifact persistence
ritel-frontend/                        Streamlit UI
agent_definitions.txt                  Editable agent definitions extracted from config.js
generate_docs.py                       Extracts agent definitions from config.js → agent_definitions.txt
publish_agents.py                      Pushes edited agent_definitions.txt back into config.js
debug_viewer.html / session_viewer.html  Local viewers for inspecting run logs and sessions
context/                               Design docs, ADK standards, to-do list
notes/                                 Prompt-engineering and agent-design research notes
```

## Editing agents

Agent prompts live in `config.js`, but there's a round-trip workflow for editing them as plain text:

```bash
python generate_docs.py    # config.js → agent_definitions.txt
# ...edit agent_definitions.txt...
python publish_agents.py   # agent_definitions.txt → config_updated.js
```

## Deployment notes

- The n8n workflows run on n8n cloud; the frontend triggers a run via the `generate-diagram` webhook.
- `render-matplotlib` deploys as a GCP Cloud Function (`functions-framework`); `ritel-state-manager` and `ritel-frontend` each have a Dockerfile for Cloud Run.
- GCP credentials are supplied via the `FIRESTORE_KEY_JSON` environment variable (falls back to application-default credentials). The frontend reads `N8N_START_URL` and `API_KEY` from the environment.
