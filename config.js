// =============================================================================
// EDEE CONFIG  —  flat ADK contract (rewritten for universal_agent_1_of_4)
//
// CONTRACT (everything Node 1 / Node 3 actually reads):
//   config.tasks[task_id]   → { assigned_agent, instruction, schema?, model_tier?,
//                               model_type?, history_scope?, result?, terminal_mode? }
//   config.agents[agent_id] → { system_identity, history_scope, model_tier?, model_type? }
//   config.model_registry / provider_by_type / default_*_tier / maximum_*_tier
//   config.api_keys / job_id / enable_gui_logging / gui_webhook_url
//
// KEY CHANGES FROM THE OLD NESTED CONFIG:
//   - phases[] is GONE. tasks and agents are flat, globally-unique registries.
//     A task points at its agent via `assigned_agent`.
//   - agent `identity` → `system_identity`.
//   - `history_scope` is no longer a keyword ("none"/"phase"/"global"). It is now a
//     LIST OF EVENT AUTHORS (agent IDs) whose events this turn may see.
//   - Templating is `{variable}` (single brace), resolved from session_state.
//     ⚠️ Node 1 THROWS if a referenced variable is not in session_state. Every {var}
//     below must be guaranteed to exist by the time its task runs.
//     ⚠️ Never put a literal `{` in an instruction — it will be read as a variable.
//   - no_model `result` is an OBJECT (not a JSON string). Its string leaves are
//     templated recursively. This is why the old `"{\"problem\": \"...\"}"` string
//     form is gone — bare braces in a JSON string collide with the {var} regex.
//   - `context_override` and `default_context_template` are GONE. Node 1 builds the
//     user turn from `instruction` alone, so context lines are now folded into the
//     top of each instruction as {var} templates.
//   - `global_task_explanation` and the per-phase `identity` blocks are folded into
//     each agent's `system_identity`.
// =============================================================================


// === 🎨 STYLE DICTIONARY ===
//storybook, casual_mobile, cel_shaded_anime, claymation_diorama, mid_century_modern
const styleLibrary = {
  "storybook": {
    // The style you should normally use is best described as
    description: "a vibrant, whimsical ink and watercolor illustration, heavily inspired by classic children's storybooks. It combines the fluidity and bright color blends of watercolors with the crisp, detailed line work of pen-and-ink drawings.",
    // THE STYLE (Aesthetics):
    aesthetic: "Define a clear, beautiful yet educational aesthetic (e.g., 'ink and watercolor wash', 'digital watercolor', 'pen and ink outlines', 'crisp ink lines', 'detailed line art', 'clear outlines', 'structured and decorative', 'whimsical storybook illustration', 'cheerful', 'vibrant and colorful')."
  },
  "casual_mobile": {
    // The style you should normally use is best described as
    description: "a highly detailed, premium digital illustration, heavily inspired by high-end casual mobile game art. It combines a vibrant, pastel-leaning color palette and soft, smooth gradient shading with crisp, clean vector-style edges, maintaining intricate details and a polished, structured aesthetic rather than being overly simplified.",
    // THE STYLE (Aesthetics):
    aesthetic: "Define a clear, beautiful, and highly detailed aesthetic (e.g., 'premium digital illustration', 'casual mobile game art style', 'vibrant pastel palette', 'smooth gradient shading', 'crisp clean vector-style edges', 'stylized but highly detailed', 'cheerful and cozy')."
  },
  "cel_shaded_anime": {
    // The style you should normally use is best described as
    description: "a crisp, high-contrast digital illustration heavily inspired by modern studio anime and cel-shaded animation. It features bold, clean line art, vibrant colors, and distinct, blocky shadow shapes that make overlapping objects and spatial relationships exceptionally easy to read.",
    // THE STYLE (Aesthetics):
    aesthetic: "Define a clear, highly readable, and energetic aesthetic (e.g., 'studio anime style', 'cel-shaded animation', 'crisp digital line art', 'bold outlines', 'flat colors with hard shadows', 'high-contrast manga cover art', 'dynamic but educational', 'vibrant and expressive')."
  },
  "claymation_diorama": {
    // The style you should normally use is best described as
    description: "a soft, tangible 3D illustration resembling a beautifully lit, physical diorama made of clay or smooth plastic. It features a tactile, miniature feel with subtle gloss and clean, rounded geometry, providing distinct and readable 3D depth for object arrangements and volumes without relying on hyper-realism. It feels like a high-end, perfectly arranged educational toy.",
    // THE STYLE (Aesthetics):
    aesthetic: "Define a tactile, miniature 3D aesthetic (e.g., 'claymation diorama', 'smooth plastic materials', 'isometric miniature', 'soft studio lighting', 'educational toy aesthetic', 'tangible 3D rendering', 'clean geometry', 'approachable and bright')."
  },
  "mid_century_modern": {
    // The style you should normally use is best described as
    description: "a highly stylized, minimalist digital illustration heavily inspired by 1950s and 1960s educational posters. It features bold, flat geometric shapes, clean vector-style edges, and a limited but vibrant retro color palette. This clutter-free approach is visually striking while keeping the focus entirely on shape, proportion, and clear spatial relationships.",
    // THE STYLE (Aesthetics):
    aesthetic: "Define a minimalist, geometric aesthetic (e.g., 'mid-century modern flat vector', '1960s educational poster style', 'bold geometric shapes', 'limited retro color palette', 'clean vector edges', 'minimalist and clutter-free', 'vintage screen print feel', 'highly stylized but readable')."
  }
};
//add emoji style

// Grab the requested style from the input data (defaults to "casual_mobile" if empty)
const selectedStyle = items[0].json.style_preference || "casual_mobile";
const activeStyle = styleLibrary[selectedStyle] || styleLibrary["casual_mobile"];


// === 🧠 MODEL REGISTRY ===
// This centralizes all URLs based on Provider -> Type -> Tier
const modelRegistry = {
  "google": {
    "text": {
      "slow": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent",
      "medium": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent",
      "fast": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
    },
    "view_img": {
      "slow": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent",
      "medium": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent",
      "fast": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
    },
    "img2img": {
      "slow": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image:generateContent",
      "medium": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image:generateContent",
      "fast": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    }
  },
  "openai": {
    "img2img": {
      "slow":   { url: "https://api.openai.com/v1/images/edits", model: "gpt-image-1.5" },
      "medium": { url: "https://api.openai.com/v1/images/edits", model: "gpt-image-1-mini" },
      "fast":   { url: "https://api.openai.com/v1/images/edits", model: "gpt-image-1-mini" }
    }
  }
};


// === 🌍 GLOBAL CONTEXT (prepended to every agent's system_identity) ===
const GLOBAL_TASK_EXPLANATION = `\
You are part of the [Educational Diagram Engineering Engine] EDEE. Your job is to create high quality illustrative diagrams for word problems in math textbooks.

CORE DIRECTIVES:
1. Precision: Diagrams must be technically correct in all respects, and not contain extraneous items, artifacts, or errors. They should have accurate dimensions & aspect ratio.
2. Clarity: Output diagrams must be elegant, intuitive, clean, high-contrast, readable, and free of clutter. Do not try to draw 2d concepts as 3d/isometric images.
3. Utility: Output diagrams must be educational and functional for their intended purpose. They shouldn't give away the answer to the problem, and they should give useful insight into the problem and/or relevant underlying concepts.
4. Aesthetics: diagrams must be colorful, easy to look at, and in a style suitable to the task. Stick to artistic/illustration style rather than realism.
5. Safety/Liability: Diagrams shouldn't contain anything that will obviously be deemed unsuitable for children. No need to nitpick, but use common sense.`;

// === COMMON BOILERPLATE DIRECTIVES ===
const DIRECTIVE_MANAGER = `\
[MANAGERIAL GUIDANCE]
As a manager, your job is to:
- review
- synthesize
- remove redundancies
- review subordinates' decisions
- address concerns
- resolve conflicts/contradictions
- double check correctness
- make the final call
- produce results for the user

For tasks with retry loops:
- decide whether a task is complete or needs to go back to subordinates for corrections
- troubleshoot problems
- direct subordinates
- make the call when to return an imperfect result rather than an error`;

// === 🧭 STAGE CONTEXT (the old per-phase `identity` blocks) ===
// Phases no longer exist as a routing construct, but the text was useful orientation
// for the agents, so it is folded into the system_identity of the agents that need it.
const STAGE_1_CONTEXT = `\
STAGE: Validation and Initial Planning
Analyze the incoming request to check whether it is valid and get it into the proper format. The goal by the end of this stage is to return:
- The original math problem, for context.
- A general description of the image we want.`;

const STAGE_2_CONTEXT = `\
STAGE: Description Refinement
Focus: Transforming a general image description into an complete, detailed, unambiguous visual description.
By the end of this stage we should have a refined, detailed description ready for an artist to actually draw.`;

const STAGE_3_CONTEXT = `\
STAGE: BASE DIAGRAM GENERATION
Focus: Transforming visual descriptions into python code, then generating a base diagram.
In this stage we will plan and draw the underlying diagram, containing figures, simple shapes, lines, text labels, and any other things requiring exact measurements for mathematical accuracy. The diagram will act as a skeleton/scaffolding for an artist to overlay the final image on in a later stage, at that point illustrations of complex objects can be added in.`;

const STAGE_4_CONTEXT = `\
STAGE: ADVANCED IMAGE GENERATION
Focus: Transforming the clean base diagram into a polished textbook illustration using ControlNets or context preserving Image-to-Image generation.

Context: The overall system works in two passes. First, we use Python (Matplotlib) to draw a mathematically precise underlying 'skeleton' or 'scaffolding'. Second, we pass that scaffolding to an AI Image Generator to paint the final, beautiful illustration over the top of it.`;

const STAGE_5_CONTEXT = `\
STAGE: MULTI-METRIC REVIEW AND POSTPROCESSING.
Focus: Final quality assurance, ensuring the illustration is biased-free, aesthetically pleasing, developmentally appropriate, and mathematically precise.`;

const STAGE_6_CONTEXT = `\
STAGE: FINAL OUTPUT and REPORT.
Focus: Returning the final image to the user if it was generated successfully, reporting success or failure with a clear explanation, and generating a detailed archival report for developers.`;

const STAGE_ERROR_CONTEXT = `\
STAGE: GLOBAL ERROR HANDLING.
Focus: Analyzing the pipeline's history when a critical error, crash, or unrecoverable loop occurs, determining the root cause, and communicating it clearly to the user.`;


// === 🧱 SITUATIONAL DIRECTIVES (canned no_model payloads) ===
// Hoisted to consts, registered in config.directive_library (bottom of file) keyed by
// their situational_planning boolean. The "cfg inject constraints" n8n node composes
// whichever subset is flagged true and hands the joined text to inject_constraints.
const DIRECTIVE_3D = `\
[3D RENDERING CONSTRAINTS]:
- Analyze the scene for 3D logic. Ensure depth cues (shading, perspective) are defined.
- 3D objects should be opaque and shaded. Prefer solid objects to transparent skeletons unless the problem statement suggests otherwise.
- Generate objects at angles and positions suitable for viewing as examples. Important features of 3D objects must be visible, not facing away from the user.
- Ensure geometric shapes are at the right scale, angle, and realistic dimensions to denote the actual real-world object they represent. In other words, estimate the length, width, and height of a real example of the object, and ensure the aspect ratio in your code is similar.`;

const DIRECTIVE_PRIMITIVES = `\
[COMPOSITION & PRIMITIVE CONSTRAINTS]:
- Break down complex objects into geometric primitives (e.g., 'circles for cats', 'white rounded rectangles for sheep').
- If an object can be modeled precisely by a few simple primitives, use them. If in doubt, fall back to circles to denote approximate size and location.
- Different classes of objects must be assigned distinctly different colors or different primitives.
- Placements (random, in a grid, etc.) and spacing must be reasonable and make sense with respect to the problem description. Ensure no unintentional overlaps.
- Think about real-world environments: A flock of geese might be in a V-shape; objects being compared for height should be side-by-side with their bases level.`;


// === 📜 HISTORY SCOPE GROUPS ===
// history_scope is now a list of EVENT AUTHORS. An agent sees an event only if the
// event's author is in this list. Its OWN events come back as role "model" (enabling
// retry/critique loops); everyone else's come back as role "user".
//
// ⚠️ NOTE ON PROMPT EVENTS: Node 1 logs each prompt with author = prompt_author, which
// defaults to "system". Scoping is by author (not by task): a prompt is only replayed
// if the event immediately after it in the log ALSO survived the author filter — i.e.
// its reply is in scope.

const STAGE1_AGENTS = ["problem_validation", "image_description"];
const STAGE2_AGENTS = ["image_detail_planner", "dimension_expert", "layout_expert", "visual_director",
                       "markup_specialist", "educator", "3d_specialist", "data_viz_expert",
                       "arrangement_planner", "artistic_planner"];
const STAGE3_AGENTS = ["selector", "scaffolding_designer", "architect", "builder", "reviewer", "inspector"];
const STAGE4_AGENTS = ["image_planner", "artist"];
const STAGE5_AGENTS = ["image_verifier", "issue_aggregator"];
const STAGE6_AGENTS = ["final_reporter"];
const ERROR_AGENTS  = ["error_handler", "error_expert", "error_injector"];

const ALL_AGENTS = [
  ...STAGE1_AGENTS, ...STAGE2_AGENTS, ...STAGE3_AGENTS,
  ...STAGE4_AGENTS, ...STAGE5_AGENTS, ...STAGE6_AGENTS, ...ERROR_AGENTS
];


// =============================================================================
// 🤖 AGENT REGISTRY
// =============================================================================
const agents = {

  // --- STAGE 1 ---------------------------------------------------------------
  "problem_validation": {
	model_tier: "medium",  // It gets confused about requested vs implied on fast.
    history_scope: [],  // "none"
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_1_CONTEXT}

IDENTITY: You are a problem validation agent. Your job is to analyze the raw incoming request & make decisions about how to handle it. The input may contain a math problem, a request for a specific diagram/image, both, or neither.`
  },

  "image_description": {
	model_tier: "medium", // planner type
    history_scope: [],  // "none"
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_1_CONTEXT}

IDENTITY: You are an expert math educator and image planning agent. Your job is to analyze a word problem and decide what illustration or diagram to create for that problem. Your goal is to decide WHAT to draw, not HOW to draw it.`
  },

  // --- STAGE 2 ---------------------------------------------------------------
  "image_detail_planner": {
    model_tier: "slow",  // manager type
    model_type: "text",
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the image detail planner. You manage the task of transforming a general image description into an complete, detailed, unambiguous visual description.

Context: The overall system works in two passes. First, we use Python (Matplotlib) to draw a mathematically precise underlying 'skeleton' or 'scaffolding'. Second, we pass that scaffolding to an AI Image Generator to paint the final, beautiful illustration over the top of it.

Your task is to describe that second pass: the final result, including technical details, [basic] artistic details, or both, as warranted. You don't need to figure out things like medium, style or aesthetic, the artist will handle that, but a general description of the scene, including objects not mentioned in the problem, if any.

For example, a simple graph will have no artistic details step, and a stock illustration of a supermarket has no technical measurements, but most of your requests will have both. Even on the graph example, you might decide to a decoration of some kind based on what the word problem is about`
  },

  "dimension_expert": {
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the Dimension Estimator. You ensure objects have realistic sizes.`
  },

  "layout_expert": {
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the Composition Planner. You manage space, composition, and layout.`
  },

  "visual_director": {
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the Visual Director. You control the camera and framing.`
  },

  "markup_specialist": {
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the Markup Specialist. You handle labels and indicators.`
  },

  "educator": {
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the Educational Enhancer. You optimize for student understanding.`
  },

  "3d_specialist": {
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the 3D Modeling Specialist.`
  },

  "data_viz_expert": {
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the Data Visualization Expert.`
  },

  "arrangement_planner": {
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the Geometric Abstraction Artist.`
  },

  "artistic_planner": {
    history_scope: STAGE2_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_2_CONTEXT}

IDENTITY: You are the Art Director. You think about artistic details and what the final illustration should have in it.`
  },

  // --- STAGE 3 ---------------------------------------------------------------
  "selector": {
    model_tier: "medium", // manager type
    history_scope: STAGE3_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_3_CONTEXT}

IDENTITY: You are the Stage 3 Workflow Orchestrator. You decide the best technical approach.`
  },

  "scaffolding_designer": {
    history_scope: STAGE3_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_3_CONTEXT}

IDENTITY: You are the Technical Scaffolding Designer. Your job is to translate a rich, artistic 'Diagram Request' into a strict, barebones geometric blueprint.

Context: The overall system works in two passes. First, we use Python (Matplotlib) to draw a mathematically precise underlying 'skeleton' or 'scaffolding'. Second, we pass that scaffolding to an AI Image Generator to paint the final, beautiful illustration over the top of it.

Your task is to design that first pass: the scaffolding.`
  },

  "architect": {
    model_tier: "medium", // planner type
    history_scope: STAGE3_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_3_CONTEXT}

IDENTITY: You are the Lead Architect. You plan data structures, plotting strategies, and primitive usage.
Your general objectives are:
- generate the right number of objects in the right positions
- don't generate unnecessary axes, grids, skeletons, or weird markings. 3d objects should be opaque and  shaded, or avoided in favor of 2d where possible.
- if instructed to add words, numbers, or other necessary markings, you can add them
- generate the objects at such angles and positions as to be suitable for viewing as examples. for example significant[to the problem] features of 3d objects need to be visible, not facing away from the user
- we prefer solid objects to transparent skeletons(see example below), unless the problem statement suggests
  otherwise.
- don't use any obscure libraries
- make sure the dimensions are correct in the diagram, and it is not stretched or squashed. Also make sure the edges aren't cut off in the figure, the limits(xmin, xmax, ymin, ymax, etc.) need to be a bit larger than the object.

These are general guidelines, use common sense depending on the individual diagram requested.
A professional artist will draw an image over top of your composition; you just need to get the composition correct. You can reason for a couple paragraphs before you start coding to think through the problem, first to plan out the composition, then to determine how to code it. Explicitly state the realistic dimensions of any objects in real-world units if dimensions were not given. Explicitly discuss composition, particularly placement. Composition should discuss what primitives/shapes we want to use, why, layout, spacing, relative scale, angle, relative position, and anything else relevant to getting everything in the right place so a professional artist can draw over top of it.`
  },

  "builder": {
    model_tier: "slow",  // coder type
    model_type: "text", // use more advanced agent to write code
    history_scope: STAGE3_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_3_CONTEXT}

IDENTITY: You are the Senior Python Developer. You write clean, executable code.`
  },

  "reviewer": {
    model_tier: "medium", // reviews code ; give a little extra power
    history_scope: STAGE3_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_3_CONTEXT}

IDENTITY: You are the Lead Code Reviewer. You check for bugs and logic errors before execution.`
  },

  "inspector": {
    model_tier: "slow",
    model_type: "view_img", // Override model for better vision
    history_scope: STAGE3_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_3_CONTEXT}

IDENTITY: You are the QA Vision Analyst. You check carefully for visual artifacts, if you see in history a QA check done by you failed and correction has already been attempted once, you'll only reject for serious issues on the second pass.`
  },

  // --- STAGE 4 ---------------------------------------------------------------
  "image_planner": {
    model_tier: "slow",
    model_type: "view_img", // Using advanced model
    history_scope: STAGE4_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_4_CONTEXT}

IDENTITY: You are the Art Director. You convert technical descriptions into artistic prompts.`
  },

  "artist": {
    model_tier: "slow",
    model_type: "img2img", // Using the advanced model
    history_scope: STAGE4_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_4_CONTEXT}

IDENTITY: You are the Illustrator Engine.`
  },

  // --- STAGE 5 ---------------------------------------------------------------
  "image_verifier": {
    model_tier: "medium",
    model_type: "view_img", // Override for high-fidelity vision checking
    history_scope: STAGE5_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_5_CONTEXT}

IDENTITY: You are the Lead Visual Quality Assurance Officer. Your job is to strictly audit educational illustrations against specific safety, quality, and accuracy metrics.`
  },

  "issue_aggregator": {
    model_tier: "slow", // manager type
    history_scope: STAGE5_AGENTS,
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_5_CONTEXT}

IDENTITY: You are the Final Gatekeeper. You review the reports from the verification specialists and make the final release decision.`
  },

  // --- STAGE 6 ---------------------------------------------------------------
  "final_reporter": {
    model_tier: "medium", // summarizes and explains
    history_scope: ALL_AGENTS,  // was history_scope "global"
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_6_CONTEXT}

IDENTITY: You are the Final Output and Reporting Agent. You act as the bridge between the Educational Diagram Engineering Engine (EDEE) and two distinct audiences: the end-user (who requested the diagram) and the development team (who maintains the engine).`
  },

  // --- ERROR HANDLING --------------------------------------------------------
  "error_handler": {
    model_tier: "medium", // summarize and explain
    history_scope: ALL_AGENTS,  // was history_scope "global"
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_ERROR_CONTEXT}

IDENTITY: You are the Diagnostics and Communication Agent. Your job is to review the complete history of a failed EDEE pipeline, figure out what went wrong, and explain it to the user in simple, non-technical terms.`
  },

  "error_expert": {
    model_tier: "slow", // manager type
    history_scope: ALL_AGENTS,  // ⚠️ was "phase" — see notes; a troubleshooter scoped to
                                //    only the error agents could not see the failure itself.
    system_identity: `\
${GLOBAL_TASK_EXPLANATION}

${STAGE_ERROR_CONTEXT}

IDENTITY: You are the Error Diagnosis Agent. Your job is to review the complete history of a failed EDEE task, analyze what went wrong, and come up with mitigation strategies.`
  },

  "error_injector": {
    history_scope: [],
    system_identity: "IDENTITY: System utility for safely formatting and logging errors into the project history."
  }
};


// =============================================================================
// 📋 TASK REGISTRY  (flat; every task names its agent via `assigned_agent`)
// =============================================================================
const tasks = {

  // --- STAGE 1: Validation & Initial Planning --------------------------------
  "extract_math": {
    assigned_agent: "problem_validation",
    history_scope: [],  // task-level override: this task starts cold
    instruction: `\
User Input: {original_query}

Locate and extract the problem from the input, if present.
1. Does the input contain a math problem?
2. If YES: Extract it VERBATIM.
3. If NO: Return null.
If there is only a request for a specific diagram, but no related math problem, return NO(null).`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "math_reasoning": { "type": "STRING", "description": "Analyze the input. Does it contain a solvable math problem?" },
        "math_found": { "type": "BOOLEAN", "description": "True if a solvable math problem is present." },
        "math_text": { "type": "STRING", "description": "The verbatim math problem text, or null if none found." }
      },
      "required": ["math_reasoning", "math_found", "math_text"]
    }
  },

  "extract_visual": {
    assigned_agent: "problem_validation",
    history_scope: [],  // task-level override: this task starts cold
    instruction: `\
User Input: {original_query}

Locate and extract the request for a specific diagram/image from the input, if present.
1. Does the user request a specific visual?
2. If YES: Extract description VERBATIM.
3. If NO: Return null.
If the user asked for something specific, the answer is YES.
Otherwise, the answer is NO, and a specialist agent will decide what to draw based on the problem.
If the user merely implied an image, or mentioned things that COULD be drawn, answer NO and let the image planner agent do its job.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "visual_reasoning": { "type": "STRING", "description": "Analyze the input. Does the user describe how the image should look, or request a specific image?" },
        "image_request_found": { "type": "BOOLEAN", "description": "True if visual instructions are present requesting a specific illustration." },
        "visual_text": { "type": "STRING", "description": "The verbatim visual description, or null if none found." }
      },
      "required": ["visual_reasoning", "image_request", "visual_text"]
    }
  },

  "extract_math_and_visual": {
    assigned_agent: "problem_validation",
    history_scope: [],  // task-level override: this task starts cold
    instruction: `\
User Input: \`\`\`{original_query}\`\`\`

Does the input contain mathematical ideas?
Does the input contain a specific, solvable math problem?
Does the input contain descriptions of objects, situations, or geometries?
Is a visual implied, but exactly what to draw TBD?
Or does the user request a specific visual?

Analyze the input according to each of those criteria, then decide the following:
Does the input contain a specific, solvable math problem? An implied math problem is not enough.
Does the user request a specific visual? An implied visual is not enough.
`,
    schema: {
      "type": "OBJECT",
      "properties": {
		"general_reasoning": { "type": "STRING", "description": "Analyze the input according to each of the 5 criteria given." },
        "math_reasoning": { "type": "STRING", "description": "Does the input contain a specific, solvable math problem? An implied math problem is not enough." },
        "math_found": { "type": "BOOLEAN", "description": "True if a specific, solvable math problem is present." },
        "math_text": { "type": "STRING", "description": "The VERBATIM math problem text, excluding visual requests or anything else not directly part of the problem. null if no math problem found." },
		"visual_reasoning": { "type": "STRING", "description": "Does the user request a specific visual? An implied visual is not enough." },
        "image_request_found": { "type": "BOOLEAN", "description": "True if a request for a specific visual is present." },
        "visual_text": { "type": "STRING", "description": "The VERBATIM visual request, and only parts of any math problem related to what to depict. null if no visual request found." }
      },
      "required": ["general_reasoning", "math_reasoning", "math_found", "math_text", "visual_reasoning", "image_request_found", "visual_text"]
    }
  },

  "check_conflict": {
    assigned_agent: "problem_validation",
    instruction: `\
User Input: \`\`\`{original_query}\`\`\`

Math Problem: {math_text}

Visual Request: {visual_text}

Analyze for conflict.

Check if the User's Visual Request contradicts the Math Problem (e.g., asking for a triangle when the problem is about a square).

If the visual request is vague or stylistic, that is VALID.
Only mark INVALID if it is factually impossible to draw both.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING", "description": "Explain the relationship between the visual request and the math constraints." },
        "valid": { "type": "BOOLEAN", "description": "True if they can coexist. False if they contradict." }
      },
      "required": ["reasoning", "valid"]
    }
  },

  "format_ready_branch": {
    assigned_agent: "problem_validation",
    model_tier: "no_model",
    instruction: "Format the payload for the 'ready' branch (both math and visual present).",
    result: { problem: "{math_text}", description: "{visual_text}" }
  },

  "format_math_only_branch": {
    assigned_agent: "problem_validation",
    model_tier: "no_model",
    instruction: "Format the payload for the 'math_only' branch.",
    // ⚠️ CHANGED: the old result also set description: "${description}", but on this branch
    // propose_diagram already added it to session_state in the previous step
    result: { problem: "{math_text}" }
  },

  "format_visual_only_branch": {
    assigned_agent: "problem_validation",
    model_tier: "no_model",
    instruction: "Format the payload for the 'visual_only' branch. Inject the missing math problem placeholder.",
    result: {
      problem: "No problem given, do your best without, use common sense.",
      description: "{visual_text}"
    }
  },

  "propose_diagram": {
    assigned_agent: "image_description",
    instruction: `\
Original Query: {original_query}
Math Problem: {math_text}

1. Analyze the problem to understand the core concept.
2. Determine if a technical diagram (Geometry, Graph, etc.) is needed, or if a simple illustrative image is better. You are part of a illustrative diagramming group - even for technical diagrams, you are generally to illustrate unless there is nothing appropriate to illustrate(for example many graphs are hard to illustrate usefully).
3. Output a high-level CONCEPTUAL description only.
You don't have to define dimensions, compositional details, specific coordinates, colors, or labels. That will be handled in the next stage. Just figure out what the most comprehensible, useful, educational, appropriate, aesthetically pleasing diagram or illustration would be. This is a pitch to send to the illustration editor, give enough information so an educator can understand what you are proposing to draw.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING", "description": "Think step-by-step. 1) Identify the math concept. 2) Evaluate if a strict diagram or general illustration is better. 3) Justify the choice." },
        "description": { "type": "STRING", "description": "The high-level conceptual description of the visual." }
      },
      "required": ["reasoning", "description"]
    }
  },

  // --- STAGE 2: Description Refinement ---------------------------------------
  "review_request": {
    assigned_agent: "image_detail_planner",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Look at the Original Query and the latest Diagram Request. Decide whether your image generation should cover technical details, artistic details, or both.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING", "description": "Analyze the query and request. Explain why technical precision, artistic illustration, or both are required." },
        "requires_technical": { "type": "BOOLEAN", "description": "True if the image needs precise measurements, graphs, charts, geometry, or exact object counts." },
        "requires_artistic": { "type": "BOOLEAN", "description": "True if the image needs detailed illustrations, real-world objects, or aesthetic decorations." }
      },
      "required": ["reasoning", "requires_technical", "requires_artistic"]
    }
  },

  "situational_planning": {
    assigned_agent: "image_detail_planner",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Determine if we need to do any case-specific planning:
1) Are there 3D solids or features in our image, particularly in the technical description?
2) Does it involve drawing graphs?
3) Are there arranged objects we can't trust the AI Image Generator to add in?
    a) we cannot easily approximate their edges with simple geometric shape[ex. a cat]
    b) need to be mathematically specific in terms of numbers[more than 3 of the same object type], relative sizes[fixed size or ratio mentioned in problem], or arrangement[object is parallel to another, at a specific xy point, part of a group arranged in a semicircle, etc.]
    Only an object that fulfills both conditions qualify. Consider both conditions for each object during reasoning.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING", "description": "Step-by-step analysis evaluating the presence of 3D features, graphs, and complex arranged objects." },
        "needs_3d_planning": { "type": "BOOLEAN", "description": "True if the diagram involves 3D solids, isometric views, or 3D features." },
        "needs_graph_planning": { "type": "BOOLEAN", "description": "True if the request involves plotting data, coordinate planes, or mathematical graphs." },
        "needs_arrangement_planning": { "type": "BOOLEAN", "description": "True if there are specific counts, sizes, or arrangements of complex real-world objects that we'll need ControlNets or context preserving Image-to-Image generation for." }
      },
      "required": ["reasoning", "needs_3d_planning", "needs_graph_planning", "needs_arrangement_planning"]
    }
  },

  "estimate_dimensions": {
    assigned_agent: "dimension_expert",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Check if the description has specific dimensions.
- If YES: Confirm them.
- If NO: Assign realistic values based on real-world logic (e.g., 'Bathtub = 60x30 inches').`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "dimensions_context": { "type": "STRING", "description": "The explicit dimensions to be used." }
      },
      "required": ["reasoning", "dimensions_context"]
    }
  },

  "plan_composition": {
    assigned_agent: "layout_expert",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Plan the layout.
1. How objects are arranged in the image.
2. How objects are arranged relative to each other(e.g., 'V-shape'[for flock], 'a grid', 'a semicircle', 'a random cluster')
3. Ensure they are visible and distinct, and [usually] non-overlapping.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "composition_plan": { "type": "STRING", "description": "Detailed layout instructions." }
      },
      "required": ["reasoning", "composition_plan"]
    }
  },

  "plan_viewpoint": {
    assigned_agent: "visual_director",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Determine the best viewing angle (e.g., side-view, cross-section, top-down, isometric, whatever) and ensure significant features are visible. That said, don't try to render a 3d/rotated illustration of a fundamentally 2d problem.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "visual_plan": { "type": "STRING", "description": "Angle, scale, and visibility rules." }
      },
      "required": ["reasoning", "visual_plan"]
    }
  },

  "plan_markings": {
    assigned_agent: "markup_specialist",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Determine necessary mathematical markups: labels, measuring lines, angle arcs, or variables (x, y).
Make sure all labels are visible, and accessible to the color-blind.
Assume the student can see both the diagram and the original problem. Does it actually warrant labels? What labels wou this sort of diagram normally have? Does reading the problem already convey all the information we need without labels?`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "marking_plan": { "type": "STRING", "description": "List of labels and lines to add." }
      },
      "required": ["reasoning", "marking_plan"]
    }
  },

  "enhance_clarity": {
    assigned_agent: "educator",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Optimize for explanation.
Should we highlight a specific part? Use specific colors to link concepts? Prioritize intuitive visuals.
A good image should help illustrate & clarify the problem, but don't do the student's work for them, or give the answer to the problem.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "educational_plan": { "type": "STRING", "description": "Enhancements for clarity." }
      },
      "required": ["reasoning", "educational_plan"]
    }
  },

  "plan_3d": {
    assigned_agent: "3d_specialist",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Describe relative depths, camera angles, and key features that need to be visible to viewer.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "3d_plan": { "type": "STRING", "description": "Specific 3D rendering rules." }
      },
      "required": ["reasoning", "3d_plan"]
    }
  },

  "plan_graph": {
    assigned_agent: "data_viz_expert",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Define the graph type (bar, line, scatter). Set axis labels, ranges, and data point styles. Choose high-contrast colors.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "graph_plan": { "type": "STRING", "description": "Graphing specifications." }
      },
      "required": ["reasoning", "graph_plan"]
    }
  },

  "plan_object_arrangement": {
    assigned_agent: "arrangement_planner",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

1. Specify the number and type of objects.
2. Ensure they are visible and distinct.
3. Define relative positions (e.g., 'V-shape for flock').`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "arrangement_plan": { "type": "STRING", "description": "Detailed list of object counts, types, and their spatial arrangement." }
      },
      "required": ["reasoning", "arrangement_plan"]
    }
  },

  "artistic_planning": {
    assigned_agent: "artistic_planner",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

Remember, the illustration is going to be generated in 2 passes, a geometric pass done via python followed by an artistic pass done via image gen. And your goal is a high quality illustration or illustrative diagram suitable for use in math textbooks. Plan details related the final artistic, aesthetically pleasing pass. If all the planning up until now has been about the geometric scaffolding and not the final result, fill in those details now. Any objects mentioned in the word problem that need to be drawn, anything not directly mentioned that should be drawn. We will be using state-of-the-art image gen, so we don't need to limit ourselves to what can be drawn with python for artistic planning. Any style, subject, or amount of detail is possible, at the proficiency of a master artist. You should try to make good use of this. It needs to be optimized for the requested task, but just don't consider artistic talent a limiting factor. In a later stage a senior art director will get another pass at this, so you don't need to describe every detail or design an image prompt. But decide what should be in the image in addition or superimposed on the geometric details, and roughly how it should be presented.

If the final artistic image is already planned, make corrections if necessary in line with these guidelines and your core directives, and add details if the description is too vague.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "artistic_plan": { "type": "STRING", "description": "Any missing artistic details." }
      },
      "required": ["reasoning", "artistic_plan"]
    }
  },

  "merge_plans": {
    assigned_agent: "image_detail_planner",
    instruction: `\
Original Query: {original_query}
Diagram Request: {description}

SYNTHESIS TASK:
1. Review the entire Project History and all Specialist plans.
2. Resolve any conflicts (e.g., if Composition says 'center' but 3D says 'isometric', decide which wins).
3. Merge, reduce, and simplify all instructions into a SINGLE, dense, rendering-ready visual description.
4. Ensure the final text covers: Layout, Composition, Dimensions, Coordinates, Shapes, Colors, Viewpoint, Labels, and specific Math Details wherever necessary.
5. That said, don't try to render a 3d/rotated illustration of a fundamentally 2d problem, it's harder to code, and it can be confusing to the viewer. No unnecessary perspective transformations that affect the scale of any geometric elements. So artsy isometric trees on a map would be fine, as long as the height of the trees was not part of the problem. Top down trees would be fine too, after mathematical constraints are satisfied, the artist will probably go with whatever is most aesthetically pleasing. This limits us artistically, but the artist needs to work within the limits of the educator, not vice-versa. If any of you planners did suggest rendering a 2d problem in 3d, I urge you to reconsider.

Remember, your job is to create high quality illustrative diagrams for word problems in math textbooks, in line with your core directives. Your output should describe such.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "conflict_resolution_notes": { "type": "STRING" },
        "latest_description": { "type": "STRING", "description": "Final detailed prompt for the artist/illustrator." }
      },
      "required": ["conflict_resolution_notes", "latest_description"]
    }
  },

  "review_description": {
    assigned_agent: "image_detail_planner",
    instruction: `\
Original Query: {original_query}
Diagram Request: {latest_description}

CRITICAL REVIEW:
1. Is the current Diagram Request still compatible with the Original Query? If they conflict, it is a problem.
2. Are dimensions realistic and specified?
3. Have you defined all necessary Layout, Composition, Dimensions, Coordinates, Shapes, Colors, Viewpoint, Labels.
4. Is any key information missing?
5. Does the figure do the student's job for them or give the solution away?
7. Are both underlying geometry and overlaid artistic details covered?
8. Is the style correct (clean, educational, etc.)?

DECISION:
- If PERFECT: Output 'ready_for_code' = true.
- If FLAWS: Output 'ready_for_code' = false and provide the FIXED description.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "critique": { "type": "STRING" },
        "ready_for_code": { "type": "BOOLEAN" },
        "latest_description": { "type": "STRING", "description": "The corrected version (or the original if perfect)." }
      },
      "required": ["critique", "ready_for_code", "latest_description"]
    }
  },

  // --- STAGE 3: Base Diagram Generation --------------------------------------
  "choose_path": {
    assigned_agent: "selector",
    instruction: `\
Original Query: {original_query}
Diagram Request: {latest_description}

Analyze the 'Diagram Request'. Determine the generation strategy.

OPTIONS:
1. 'STANDARD_DIAGRAM': Standard. Use Python/Matplotlib to draw the diagram (Geometry, Graphs, Physics). We will be able to use this initial diagram as a ControlNet / basis for the composition of an AI image later, essentially draw over top of it. So this covers any case where we need some parts of the image to be mathematically accurate that doesn't fall into the COMPOSITE_PRIMITIVES category.
2. 'COMPOSITE_PRIMITIVES': Contains significant numbers of objects in one or more categories where the number of objects is part of the math problem. This is probably not necessary where there are under 3 types of objects, there's no required grouping or positioning, and total object count in each category is 3 or less. In that case choose whichever of the other categories fits best, and the artist will need to free-draw the objects in question.
3. 'DIRECT_IMAGE_GEN': The request is purely artistic, with no underlying geometric scaffolding required. Skip coding, go straight to Image Gen.
`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING", "description": "Why this path?" },
        "selected_workflow": { "type": "STRING", "enum": ["STANDARD_DIAGRAM", "DIRECT_IMAGE_GEN", "COMPOSITE_PRIMITIVES"] }
      },
      "required": ["reasoning", "selected_workflow"]
    }
  },

  "inject_constraints": {
    assigned_agent: "scaffolding_designer",
    model_tier: "no_model",
    instruction: "Apply situational constraints.",
    // Directives are consumed from HISTORY (the event this task logs), not from state —
    // hoist keeps the big text from persisting in session_state after the turn.
    hoist_result_fields: ["situational_directives"],
    // The "cfg inject constraints" n8n node composes any subset of
    // config.directive_library (based on the situational_planning booleans in
    // session_state) into a top-level `situational_directives` field. Node 1 sweeps
    // it into session_state, and this result templates it back out — logging the
    // composed text as a scaffolding_designer event so design_scaffolding sees it
    // in Project History. Replaces the old fixed inject_3d_constraints /
    // inject_primitives_constraints / inject_multiple_constraints trio, which
    // couldn't scale to arbitrary subsets.
    result: { situational_directives: "{situational_directives}" }
  },

  "design_scaffolding": {
    assigned_agent: "scaffolding_designer",
    instruction: `\
Original Query: {original_query}
Diagram Request: {latest_description}

Analyze the 'Diagram Request'. Strip away all the artistic flair, textures, and complex subjects, and describe ONLY the geometric shapes, lines, labels, and spatial boundaries that Python needs to plot. Keep things simple.

DIRECTIVES:
1. Identify the Math: What counts, shapes, graphs, grids, lines, or angles are part of the problem and must be perfectly accurate? These must be plotted.
2. Check for Situational Directives: Look at the most recent entries in your 'Project History'. If you previously injected '[3D RENDERING CONSTRAINTS]' or '[COMPOSITION & PRIMITIVE CONSTRAINTS]', you MUST follow them implicitly when designing this scaffolding. If present, they supersede these general directives in case of conflict.
3. Abstract Complex Objects: If the request asks for a "farmer standing next to a tractor", you do not plot a farmer. If their precise positions/sizes etc. are part of the word problem, you should have situational directives to follow. If their positions do not need to be pixel perfect, you can leave them off, the artist will add them later. You generally do not need to draw objects that are not part of the math problem, the artist can handle them.
4. Output Format: Provide a clear, structured blueprint of exactly what shapes to draw, where to place them relative to each other, and what colors/labels to use for the underlying Python plot. Do NOT write code.
5. Keep it simple and elegant -> Precision, Clarity, Utility. The artist is very skilled and can add details later. You only need to show
  - Shapes, angles, distances and object counts mentioned in the problem, without you the artist will estimate.
  - Key colors only, you actually shouldn't color much. The artist can recolor, you really only need to use color where the artist specifically needs it to provide clarity.
  - Any text, labels, or tick marks mentioned in the diagram request. The artist can make mistakes drawing these from scratch`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING", "description": "Explain what parts of the request require precise geometric plotting vs what parts will be abstracted as placeholders." },
        "scaffolding_blueprint": { "type": "STRING", "description": "The exact geometric instructions for the Python coder (e.g., 'Draw a 3x4 rectangle at 0,0. Draw a circle at 5,0. Add a line connecting them. Label the line x.')." }
      },
      "required": ["reasoning", "scaffolding_blueprint"]
    }
  },

  "plan_logic": {
    assigned_agent: "architect",
    instruction: `\
Original Query: {original_query}
Diagram Request: {latest_description}

Analyze the 'Original Query', 'Diagram Request', and especially the 'scaffolding_blueprint' from history. Plan the Python workflow.
1. Select Libraries (matplotlib, mplot3d).
2. Primitives: If complex objects (e.g., 'a cat') are needed, Plan to load them as PNGs (e.g., cat_primitive.png) using plt.imread.
3. Plan Drawing Order (Background -> Foreground).
4. Style Strategy (colors, alpha).`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING", "description": "Analysis of the requirements." },
        "required_libraries": { "type": "ARRAY", "items": { "type": "STRING" } },
        "required_primitives": { "type": "ARRAY", "items": { "type": "STRING" }, "description": "List of PNG filenames needed (e.g. ['cat.png'])." },
        "execution_plan": { "type": "STRING", "description": "Step-by-step logic for the coder." }
      },
      "required": ["reasoning", "required_libraries", "required_primitives", "execution_plan"]
    }
  },

  "write_code": {
    assigned_agent: "builder",
    // python_code is big and single-use: the executor reads it directly off this turn's
    // output, and reviewers see it via scoped history. Hoist it to a top-level field so it
    // never enters session_state and isn't copied into every downstream envelope.
    hoist_result_fields: ["python_code"],
    instruction: `\
Write the Python code based on the execution plan.

STRICT CONSTRAINTS:
1. Use 'matplotlib' to construct the requested geometry.
2. Focus ONLY on drawing the mathematical shapes, lines, and text in the correct relative positions.
3. DO NOT include plt.show(), plt.savefig(), plt.close(), or plt.clf(). The execution environment handles rendering and saving automatically.
4. DO NOT manually adjust margins, aspect ratios, or turn off axes (e.g., skip plt.axis('off') or plt.gca().set_aspect('equal')). The system wrapper will automatically enforce mathematical 1:1 aspect ratios, strip all grids/ticks, and crop whitespace after your code runs.
5. PRIMITIVES: If the plan asks for images, assume files like 'cat.png' exist in the local directory. Load them using plt.imread() and display with imshow or OffsetImage.
6. Output ONLY the raw, runnable Python code without markdown blocks or explanations.
7. SCOPE RESTRICTION: Your code runs in an exec() environment where helper functions cannot access global variables or top-level imports. Therefore, for any helper function you write:
  You MUST pass all script-level objects (like ax, fig, or color variables) into the function as arguments.
  You MUST place any required module imports (e.g., import matplotlib.patches as patches) directly inside the function body.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "python_code": { "type": "STRING", "description": "The complete, runnable Python script." },
        "explanation": { "type": "STRING", "description": "Brief explanation of the plotting logic." }
      },
      "required": ["python_code", "explanation"]
    }
  },

  "syntax_check": {
    assigned_agent: "reviewer",
    instruction: `\
Analyze the generated Python Code.
CHECKS:
1. Are there syntax errors?
2. Are forbidden libraries used?
3. Are variables defined before use?`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING" },
        "critique": { "type": "STRING", "description": "List of technical errors." },
        "passed_syntax": { "type": "BOOLEAN" }
      },
      "required": ["analysis", "critique", "passed_syntax"]
    }
  },

  "logic_check": {
    assigned_agent: "reviewer",
    instruction: `\
Original Query: {original_query}
Diagram Request: {latest_description}

Compare the code against the 'Diagram Request' and 'Execution Plan'.
CHECKS:
1. Does it draw ALL requested objects?
2. Are colors/styles correct?
3. Is the logic sound for the specific math problem?`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING" },
        "critique": { "type": "STRING", "description": "List of logic discrepancies." },
        "passed_logic": { "type": "BOOLEAN" }
      },
      "required": ["analysis", "critique", "passed_logic"]
    }
  },

  "verify_adherence": {
    assigned_agent: "inspector",
    instruction: `\
Original Query: {original_query}
Diagram Request: {latest_description}

Compare the rendered image against the 'Diagram Request'.
CHECKS:
- Are all necessary objects present?
- Does the illustration accurately represent the geometry/graph described in the original request?
- Are the shapes/geometry mathematically correct?
- Are labels legible and correctly placed?
- Are the colors/styles generally correct?
- Do relative sizes match the problem?
- Are vertices properly connected?`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING", "description": "What objects do you see?" },
        "critique": { "type": "STRING", "description": "Discrepancies from the request." },
        "passed_adherence": { "type": "BOOLEAN" }
      },
      "required": ["analysis", "critique", "passed_adherence"]
    }
  },

  "verify_perspective": {
    assigned_agent: "inspector",
    instruction: `\
Detect and troubleshoot problems with 3d perspective:
- Is a fundamentally 2d problem drawn in 3d? Unless the image request specifically asked you to do this, you should probably go back and rewrite your code.
- If the diagram is 3d and needs to be, is perspective wonky, or interfering with measurement accuracy? Are things in the foreground aligned or superimposed incorrectly with things in the background, or measuring lines lined up with the wrong parts of 3d objects?
- is anything else related to perspective irregular? note it in analysis.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING", "description": "What objects do you see?" },
        "critique": { "type": "STRING", "description": "Discrepancies from the request." },
        "passed_perspective": { "type": "BOOLEAN" }
      },
      "required": ["analysis", "critique", "passed_perspective"]
    }
  },

  "check_overlaps": {
    assigned_agent: "inspector",
    instruction: `\
Analyze the text labels and object placement.
CHECKS:
1. Is any text overlapping a line or object?
2. Is any text cut off at the edge?
3. Are objects overlapping in a way that obscures meaning?`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING", "description": "Assessment of spacing and labels." },
        "critique": { "type": "STRING", "description": "Locations of overlaps/cut-offs." },
        "passed_overlaps": { "type": "BOOLEAN" }
      },
      "required": ["analysis", "critique", "passed_overlaps"]
    }
  },

  "detect_artifacts": {
    assigned_agent: "inspector",
    instruction: `\
Check for technical rendering failures.
CHECKS:
1. Is the image blank or white?
2. Are axes, ticks, or grids visible (They must be HIDDEN)?
3. Is the aspect ratio distorted (circles looking like ovals)?`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING" },
        "critique": { "type": "STRING", "description": "List of technical glitches." },
        "passed_artifacts": { "type": "BOOLEAN" }
      },
      "required": ["analysis", "critique", "passed_artifacts"]
    }
  },

  // --- STAGE 4: Advanced Image Generation ------------------------------------
  "plan_finishing": {
    assigned_agent: "image_planner",
    instruction: `\
Original Query: {original_query}
Basic illustration request: {description}
Underlying Math Problem: {problem}
Base Diagram Requested & Drawn: {scaffolding_blueprint}
Final Illustration Requested: {latest_description}

Review the 'Base Diagram', both the prompt and the actual image.

Create a detailed prompt for the Artist AI to turn this into the High-Quality Math Textbook Illustration detailed in 'Final Illustration Requested'.

You will be using context preserving image to image, meaning you must write a prompt that explicitly anchors the mathematical structure of the base diagram while defining the aesthetic alterations.

The style you should normally request is best described as ${activeStyle.description}

You are an artist. If the object to draw is too generic or lacks necessary detail, you have creative license to make changes or add specifics here. For example, if the image request asks for a box of cereal but doesn't say what kind of cereal, you can make up a theme and related details. You are expected to generate something interesting and beautiful. If the image request just asks you to draw something very generic or with poor aesthetics, fix it.

We want the image neither too cluttered nor too sparse. If the description is too bare-bones, add some objects or details, this needs to be both art AND 100% functional.

Directives:
1. THE ANCHOR (Structure & Math): Explicitly instruct the Artist AI to lock the exact geometry, aspect ratios, spatial relationships, measuring lines, and text labels of the base diagram. Include strict negative constraints: do not hallucinate new numbers, do not warp straight lines, and do not alter angles.
2. THE ALTERATION (Subjects & Context): Instruct the Artist to draw the background, items, and objects described in the final illustration request. If the base diagram uses geometric primitives for physical objects (e.g., circles for apples, a line for a ladder), command the Artist to morph those primitives into the described objects WITHOUT expanding beyond their original bounding boxes or center points. The artist will need to add any objects mentioned in the final illustration request that didn't need to be shown in the scaffolding.
3. THE STYLE (Aesthetics): ${activeStyle.aesthetic} No photorealism, draw on a clean white background.

To summarize, you need everything that makes a well written image prompt, plus you need to comprehensively cover what stays the same, what gets added, what gets removed, and what gets replaced with what.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING" },
        "image_prompt": { "type": "STRING", "description": "The prompt for the image generator." },
        "style_guidelines": { "type": "STRING", "description": "Negative prompts or style constraints." }
      },
      "required": ["reasoning", "image_prompt", "style_guidelines"]
    }
  },

  "render_final": {
    assigned_agent: "artist",
    instruction: `\
Original Query: {original_query}
Basic illustration request: {description}
Underlying Math Problem: {problem}
Base Diagram Requested & Drawn: {scaffolding_blueprint}
Final Illustration Requested: {latest_description}
Detailed Image Prompt: {image_prompt}

Transform the provided Base Diagram into a final illustration.

Use the detailed image prompt provided by the image_planner(Art Director) in the previous step, refining further if necessary, always in light of the overall objective. If anything is left out, is sub-optimal, or needs touching up, you have permission to use a little artistic license.

The style you should normally use is best described as ${activeStyle.description}

We want the image neither too cluttered nor too sparse. If the description is too bare-bones, add some objects or details, this needs to be both art AND 100% functional.

NO PERSPECTIVE SHIFTS: Don't try to render a 3d illustration over a 2d problem / base diagram. At best it will be confusing, at worst the aspect ratios will now be inaccurate due to 3d perspective not being accounted for. Keep the camera framing and perspective exactly identical to the base input. For 2d geometry problems, explicitly use something like flat art, model sheet, sectional view, profile view, orthographic, etc., depending on the situation. For views from above, you can use isometric if it doesn't interfere with the accuracy, or top orthographic, flat lay illustration, top-down art, god's eye view, cartographic, etc.

CONSTRAINT: You must preserve the geometry of anything related to the original problem exactly, even if you add, remove, or transform objects.

If you can't find 'Base Diagram Requested & Drawn', 'Final Illustration Requested',  or 'Detailed Image Prompt', go ahead and draw an error message explaining instead of the requested image.`,
    schema: {} // Schema ignored for binary output
  },

  // --- STAGE 5: Multi-Metric Review ------------------------------------------
  "review_bias": {
    assigned_agent: "image_verifier",
    instruction: `\
Analyze the image for cultural bias or stereotypes. Ensure diverse representation if people are present, and avoid stereotypical depictions of roles or environments. Ensure the content is neutral and inclusive.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING" },
        "bias_detected": { "type": "BOOLEAN" },
        "score": { "type": "INTEGER", "description": "1-10 scale (10 is perfectly neutral)" }
      },
      "required": ["analysis", "bias_detected", "score"]
    }
  },

  "review_aesthetics": {
    assigned_agent: "image_verifier",
    instruction: `\
Evaluate visual appeal. Check for color cohesion, composition balance, clarity of the main subject, and absence of generated artifacts (glitches, blur, distortion).`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING" },
        "artifacts_detected": { "type": "BOOLEAN" },
        "aesthetics_score": { "type": "INTEGER", "description": "1-10 scale" }
      },
      "required": ["analysis", "artifacts_detected", "aesthetics_score"]
    }
  },

  "review_safety": {
    assigned_agent: "image_verifier",
    instruction: `\
Ensure the image is child-safe and developmentally appropriate for K-12 students. Check for any frightening elements, violence, or inappropriate themes.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING" },
        "is_safe": { "type": "BOOLEAN" },
        "flagged_elements": { "type": "STRING" }
      },
      "required": ["analysis", "is_safe", "flagged_elements"]
    }
  },

  "review_math": {
    assigned_agent: "image_verifier",
    model_tier: "slow", // use better model for technical question
    instruction: `\
Original Query: {original_query}
Math Problem: {problem}
Diagram Request: {latest_description}

Verify mathematical precision.
- Does the illustration accurately represent the geometry/graph described in the original request?
- Are labels legible and correctly placed?
- Do relative sizes match the values?
- Is perspective wonky, or interfering with accuracy in a 2d problem?`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "analysis": { "type": "STRING" },
        "math_accurate": { "type": "BOOLEAN" },
        "discrepancies": { "type": "STRING" }
      },
      "required": ["analysis", "math_accurate", "discrepancies"]
    }
  },

  "aggregate_feedback": {
    assigned_agent: "issue_aggregator",
    instruction: `\
Review the outputs from the Image Verifier's tasks (Bias, Aesthetics, Safety, Math) in the history. Summarize all findings.

DECISION LOGIC:
- If ANY critical failure (Unsafe, Bias, Math Error, Severe Artifacts) is found, set 'final_pass' to FALSE.
- If 'final_pass' is FALSE, provide a 'warning_message' and clear 'fix_instructions' for the previous stage.
- If minor issues only, you may pass the image, and just leave notes about the issues in your analysis.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "summary_analysis": { "type": "STRING", "description": "Summarize, review, and analyze findings. Make a final decision." },
        "final_pass": { "type": "BOOLEAN" },
        "notes": { "type": "STRING", "description": "Any notes on minor issues discussed in analysis." },
        "warning_message": { "type": "STRING" },
        "fix_instructions": { "type": "STRING", "description": "Specific feedback for the Image Planner/Artist to correct the issues." }
      },
      "required": ["summary_analysis", "final_pass", "warning_message", "fix_instructions"]
    }
  },

  // --- STAGE 6: Final Output & Report ----------------------------------------
  "generate_user_message": {
    assigned_agent: "final_reporter",
    // ⚠️ DECISION NEEDED: the old config had no terminal_mode here, so "completed" was
    // presumably broadcast by a separate 'final broadcast' node. If you'd rather Node 3
    // emit it, uncomment:
    // terminal_mode: { status: "completed", message_field: "user_message" },
    instruction: `\
Review the entire project history and determine the final outcome of the user's request.
Draft a message directly to the user.
- If the generation was successful, present the diagram enthusiastically and briefly explain the visual/educational choices made.
- If the generation failed (e.g., caught in an error loop, failed strict QA, or had conflicting instructions), explain clearly and politely what went wrong.
- If the user's directions caused difficulty, gently explain how(whether or not the generation was successful).
- Adopt a conversational, helpful tone. The user may reply to this message in the future to ask for changes, so keep the door open for further clarification.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "user_message": { "type": "STRING", "description": "The friendly, clear message addressed to the user." },
        "generation_successful": { "type": "BOOLEAN", "description": "True if the pipeline produced a final, QA-passed image. False otherwise." }
      },
      "required": ["user_message", "generation_successful"]
    }
  },

  "generate_archival_report": {
    assigned_agent: "final_reporter",
    instruction: `\
Review the entire project history and generate a detailed post-mortem archive report for the developers.
Summarize the entire endeavor from validation through final QA. You must explicitly highlight:
- Anything unusual about the request or the routing path taken.
- Unforeseen issues, runtime errors, or QA rejections (e.g., overlapping text, bad 3D perspective, failed syntax).
- Things that had to be fixed during the process (e.g., retry loops triggered by the Reviewers or Inspectors).
- Things that ultimately could not be fixed.
- Constructive, analytical feedback on how the EDEE system, agent prompts, or workflow routing could be improved based on this specific run.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "archival_report": { "type": "STRING", "description": "The detailed developer post-mortem report." },
        "system_improvement_suggestions": { "type": "ARRAY", "items": { "type": "STRING" }, "description": "Actionable suggestions for the dev team." }
      },
      "required": ["archival_report", "system_improvement_suggestions"]
    }
  },

  // --- ERROR HANDLING --------------------------------------------------------
  "report_error": {
    assigned_agent: "error_handler",
    terminal_mode: {
      status: "failed",
      message_field: "error_message"
    },
    // Error details arrive via the catch path: log_error injects {error_report} into
    // history as an error_injector event, which this agent's ALL_AGENTS scope can see.
    instruction: `\
An error has triggered a pipeline termination.
Review the entire 'Project History' to understand what happened.
1. Identify exactly where and why the process failed (e.g., validation rejection, python coding errors, rendering glitches, correction loop between agents fails repeatedly, etc.).
2. Draft a clear, polite, and simple explanation for the user. Do NOT use overly technical jargon (e.g., avoid mentioning 'JSON parsing', 'base64', 'Cloud Run', or 'API endpoints'). Instead, explain the *concept* of what failed (e.g., "We couldn't quite figure out the geometry for the math problem," or "Our digital artist got stuck trying to arrange the objects").
3. If applicable based on the failure, give the user a helpful tip on how they might adjust their prompt to succeed next time.
4. Keep the tone friendly, apologetic, and encouraging.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING", "description": "Internal diagnostic analysis of the failure based on the history log." },
        "error_message": { "type": "STRING", "description": "The simple, user-friendly explanation of what went wrong." }
      },
      "required": ["reasoning", "error_message"]
    }
  },

  "report_unknown_error": {
    assigned_agent: "error_handler",
    terminal_mode: {
      status: "failed",
      message_field: "error_message"
    },
    instruction: `\
A critical error or unrecoverable loop has occurred, and the pipeline has been aborted.
Review the entire 'Project History' to understand what happened.
1. Identify exactly where and why the process failed (e.g., validation rejection, python coding errors, rendering glitches, correction loop between agents fails repeatedly, etc.).
2. Draft a clear, polite, and simple explanation for the user. Do NOT use overly technical jargon (e.g., avoid mentioning 'JSON parsing', 'base64', 'Cloud Run', or 'API endpoints'). Instead, explain the *concept* of what failed (e.g., "We couldn't quite figure out the geometry for the math problem," or "Our digital artist got stuck trying to arrange the objects").
3. If applicable based on the failure, give the user a helpful tip on how they might adjust their prompt to succeed next time.
4. Keep the tone friendly, apologetic, and encouraging.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "reasoning": { "type": "STRING", "description": "Internal diagnostic analysis of the failure based on the history log." },
        "error_message": { "type": "STRING", "description": "The simple, user-friendly explanation of what went wrong." }
      },
      "required": ["reasoning", "error_message"]
    }
  },

  "troubleshoot": {
    assigned_agent: "error_expert",
    instruction: `\
A recent generation or review step has failed. Your job is to act as the lead diagnostic engineer.
Review the recent 'Project History' to troubleshoot the failure before we attempt a retry.

Walk through the problem logically:
1. OBSERVATION: What does the error log or history say went wrong? What exactly are we seeing in the current output?
2. DIAGNOSIS: Why is that wrong? Can you figure out the root cause of the failure?
3. TARGET: What is the correct behavior or output we actually need?
4. PROGRESS: evaluate the pipeline's progress
  - Are we repeating the exact same error multiple times?
  - If we are stuck in a loop and no progress is being made, maybe it's time to change the paradigm, or change the approach.
  - Are our directions fundamentally flawed; do they make sense?
5. FIX: Provide clear, actionable steps or code adjustments to fix it in the next attempt.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "error_observation": { "type": "STRING", "description": "What went wrong based on the logs/history?" },
        "error_diagnosis": { "type": "STRING", "description": "The root cause of why it failed." },
        "target_solution": { "type": "STRING", "description": "The ideal outcome we actually want." },
        "progress": { "type": "STRING", "description": "Evaluation of our progress if we've made multiple attempts" },
        "actionable_fix": { "type": "STRING", "description": "Specific adjustments or new instructions to fix the issue." }
      },
      "required": ["error_observation", "error_diagnosis", "target_solution", "progress", "actionable_fix"]
    }
  },

  "troubleshoot_visual": {
    assigned_agent: "error_expert",
    model_type: "view_img",
    instruction: `\
A recent image generation or visual review step has failed. Your job is to act as the lead visual diagnostic engineer.
Review the provided image and the recent 'Project History' to troubleshoot the failure before we attempt a retry.

Walk through the problem logically:
1. OBSERVATION: What exactly are we seeing in the current output? What visually looks wrong?
2. DIAGNOSIS: Why is that wrong? Can you figure out the root cause of the visual failure?
3. TARGET: What is the correct visual behavior or output we actually need?
4. PROGRESS: evaluate the pipeline's progress
  - Are we repeating the exact same visual error multiple times?
  - If we are stuck in a loop and no progress is being made, maybe it's time to change the paradigm, or change the approach.
  - Are our visual directions fundamentally flawed; do they make sense?
5. FIX: Provide clear, actionable steps, layout adjustments, or new prompt instructions to fix it in the next attempt.`,
    schema: {
      "type": "OBJECT",
      "properties": {
        "visual_observation": { "type": "STRING", "description": "What is currently visible/wrong in the output?" },
        "error_diagnosis": { "type": "STRING", "description": "The root cause of why the render failed." },
        "target_solution": { "type": "STRING", "description": "The ideal visual outcome we actually want." },
        "progress": { "type": "STRING", "description": "Evaluation of our progress if we've made multiple attempts." },
        "actionable_fix": { "type": "STRING", "description": "Specific adjustments, layout tweaks, or new instructions to fix the issue." }
      },
      "required": ["visual_observation", "error_diagnosis", "target_solution", "progress", "actionable_fix"]
    }
  },

  "log_error": {
    assigned_agent: "error_injector",
    model_tier: "no_model",
    instruction: "Record pipeline error into history.",
    // ⚠️ {error_text} must be supplied by the orchestrator (as a top-level field, which
    // Node 1 sweeps into session_state via externalVars). If it is absent, Node 1 throws.
    result: { error_report: "{error_text}" }
  }
};


// =============================================================================
// ⚙️ CONFIG OBJECT
// =============================================================================
const config = {
  // === 🔑 API SETTINGS ===
  "api_keys": {
    "google": items[0].json.google_api_key,
    "openai": items[0].json.openai_api_key
    },

  // === ⚙️ PROVIDER & MODEL ROUTING ===
  "provider_by_type": { text: "google", view_img: "google", img2img: "google" },
  "model_registry": modelRegistry,

  // Default tiers if an agent doesn't specify one
  "default_text_tier": "fast",
  "default_image_tier": "medium",

  // Allow the caller to toggle a speedup (caps the slowest allowed tier)
  "maximum_text_tier": items[0].json.max_text_tier || "slow",
  "maximum_image_tier": items[0].json.max_image_tier || "slow",

  //  === JOB ID ===
  "job_id": items[0].json.job_id,

  // === 📡 GUI BROADCAST SETTINGS ===
  "enable_gui_logging": true,
  "gui_webhook_url": "https://ritel-state-manager-194521282716.us-south1.run.app/update-state",

  // === 🧱 SITUATIONAL DIRECTIVE LIBRARY ===
  // Keyed by the situational_planning schema's boolean flags. The "cfg inject
  // constraints" n8n node composes the subset of directives whose flag is true in
  // session_state and feeds the joined text to the inject_constraints task — adding
  // a new constraint type = one schema boolean + one entry here. No new tasks.
  "directive_library": {
    "needs_3d_planning": DIRECTIVE_3D,
    "needs_arrangement_planning": DIRECTIVE_PRIMITIVES
  },

  // === 📚 REGISTRIES (the flat ADK contract Node 1 reads) ===
  "agents": agents,
  "tasks": tasks
};


// =============================================================================
// 🚀 SEED THE SESSION
// Node 1 destructures session_state / session_events / config off the incoming item.
// Anything else at top level is swept into session_state as a variable, so we seed
// explicitly rather than spreading items[0].json (which would leak api_key/job_id into
// session_state, and from there into any prompt that stringifies state).
//
// If the incoming item already carries config / session_state / session_events (i.e.
// this is a phase 2+ re-run), pass them through untouched instead of rebuilding —
// config is only truly built once, at phase 1, where style_preference exists at top
// level and gets baked into the instruction strings.
//
// scaffolding_blueprint is pre-seeded because the DIRECT_IMAGE_GEN path skips
// design_scaffolding entirely — without the seed, {scaffolding_blueprint} in the Stage 4
// prompts would throw a TEMPLATE ERROR. The artist's instruction already handles the
// "can't find the base diagram" case.
// =============================================================================
const incoming = items[0].json;
return [{
  json: {
    config: incoming.config || config,
    session_state: incoming.session_state || {
      original_query: incoming.original_query,
      scaffolding_blueprint: "No scaffolding image."
    },
    session_events: incoming.session_events || []
  }
}];