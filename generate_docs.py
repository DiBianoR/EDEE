import re
import json


# ==========================================
# 1. PARSER ENGINE (JS -> Python Dictionary)
# ==========================================

def strip_js_comments(line):
    """Safely strips // comments from a line of JS, ignoring URLs inside quotes."""
    in_string = False
    quote_char = None
    escape_next = False

    for i, char in enumerate(line):
        if escape_next:
            escape_next = False
            continue

        if char == '\\':
            escape_next = True
            continue

        if char in ('"', "'", '`'):
            if not in_string:
                in_string = True
                quote_char = char
            elif quote_char == char:
                in_string = False

        elif not in_string and char == '/' and i + 1 < len(line) and line[i + 1] == '/':
            return line[:i].rstrip()

    return line.rstrip()


def parse_config_to_dict(input_file="config.js"):
    """Parses the JS config object into a Python dictionary using indentation as depth."""
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: Could not find {input_file}")
        return None

    config_dict = {}
    stack = [config_dict]

    in_config = in_multiline_string = in_multiline_array = False
    multiline_key = None
    multiline_buffer = []

    for line_num, line in enumerate(lines, 1):
        raw = line.rstrip('\r\n')

        # --- SAFE COMMENT & WHITESPACE STRIPPING ---
        if not in_multiline_string and not in_multiline_array:
            raw = strip_js_comments(raw)

            # Only skip empty lines if we aren't inside a string
            if not raw.strip():
                continue

        if not in_config:
            if re.search(r'^[ \t]*const\s+config\s*=\s*\{', raw):
                in_config = True
            continue

        if not in_multiline_string and not in_multiline_array and raw.strip().startswith('};'):
            break

        if in_multiline_string:
            if re.search(r'(?<!\\)`', raw):
                content = raw.split('`')[0]
                multiline_buffer.append(content)
                final_str = "\n".join(multiline_buffer)
                if final_str.startswith('\\\n'): final_str = final_str[2:]
                stack[-1][multiline_key] = final_str
                in_multiline_string = False
            else:
                multiline_buffer.append(raw)
            continue

        if in_multiline_array:
            if ']' in raw:
                stack[-1][multiline_key] = multiline_buffer
                in_multiline_array = False
            else:
                m = re.search(r'["\']([^"\']+)["\']', raw)
                if m: multiline_buffer.append(m.group(1))
            continue

        stripped = raw.strip()
        if stripped in ['}', '},', ']', '],']: continue

        leading_ws = re.match(r'^[ \t]*', raw).group(0)
        indent = len(leading_ws.expandtabs(4))

        if indent % 2 != 0: raise IndentationError(f"Line {line_num}: Odd spaces ({indent}).\n-> {raw}")

        depth = indent // 2
        if depth > len(stack): raise IndentationError(f"Line {line_num}: Indented too deep!\n-> {raw}")
        if depth < len(stack): stack = stack[:depth]

        m = re.match(r'^["\'`]?([a-zA-Z0-9_\-]+)["\'`]?:\s*(.*)', stripped)
        if not m: continue

        key, val_raw = m.group(1), m.group(2)
        if val_raw.endswith(','): val_raw = val_raw[:-1].strip()

        if val_raw.startswith('{'):
            if re.search(r'\}\,?$', val_raw):
                inline_obj = {}
                t_match = re.search(r'["\']type["\']\s*:\s*["\']([^"\']+)["\']', val_raw)
                if t_match: inline_obj["type"] = t_match.group(1)
                d_match = re.search(r'["\']description["\']\s*:\s*(["\'])(.*?)(?<!\\)\1', val_raw)
                if d_match: inline_obj["description"] = d_match.group(2)
                e_match = re.search(r'["\']enum["\']\s*:\s*\[(.*?)\]', val_raw)
                if e_match: inline_obj["enum"] = [x.strip(" '\"") for x in e_match.group(1).split(',') if x.strip()]
                i_match = re.search(r'["\']items["\']\s*:\s*(\{.*?\})', val_raw)
                if i_match:
                    inner_obj = {}
                    it_match = re.search(r'["\']type["\']\s*:\s*["\']([^"\']+)["\']', i_match.group(1))
                    if it_match: inner_obj["type"] = it_match.group(1)
                    inline_obj["items"] = inner_obj
                stack[-1][key] = inline_obj
            else:
                new_obj = {}
                stack[-1][key] = new_obj
                stack.append(new_obj)
            continue

        if val_raw.startswith('['):
            if val_raw.endswith(']'):
                elements = val_raw[1:-1]
                stack[-1][key] = [x.strip(" '\"") for x in elements.split(',') if x.strip()]
            else:
                in_multiline_array = True
                multiline_key = key
                multiline_buffer = []
            continue

        if val_raw.startswith('`'):
            if re.search(r'(?<!\\)`', val_raw[1:]):
                stack[-1][key] = val_raw[1:val_raw.rfind('`')]
            else:
                in_multiline_string = True
                multiline_key = key
                multiline_buffer = [val_raw[1:]]
            continue

        if val_raw.startswith(('"', "'")):
            stack[-1][key] = val_raw[1:-1] if val_raw.endswith(val_raw[0]) else val_raw
            continue

        if val_raw == "true":
            stack[-1][key] = True
        elif val_raw == "false":
            stack[-1][key] = False
        elif val_raw == "null":
            stack[-1][key] = None
        elif val_raw.replace('.', '', 1).isdigit():
            stack[-1][key] = float(val_raw) if '.' in val_raw else int(val_raw)
        else:
            stack[-1][key] = val_raw

    return config_dict


# ==========================================
# 2. NESTED VALIDATOR & FORMATTER ENGINE
# ==========================================

def validate_and_format(config):
    out = ["=== EDEE AGENT DEFINITIONS ===\n\n"]

    # --- ROOT LEVEL ---
    allowed_root = {
        'api_key', 'job_id', 'enable_gui_logging', 'gui_webhook_url',
        'active_provider', 'model_registry',
        'default_text_tier', 'default_image_tier', 'maximum_text_tier', 'maximum_image_tier',
        'default_context_template', 'global_task_explanation', 'phases'
    }
    required_root = {'phases'}

    for req in required_root:
        if req not in config:
            raise ValueError(f"CRITICAL: Missing required Root level field: '{req}'")

    for k, v in config.items():
        if k not in allowed_root:
            print(f"WARNING: Extraneous Root field detected -> '{k}'")

        # Format Top-Level Text Blocks
        if k in ['default_context_template', 'global_task_explanation']:
            out.append(f"[{k.upper()}]\n{v}\n[END {k.upper()}]\n\n")

    # --- PHASES LEVEL ---
    phases = config.get('phases', {})
    for p_id, p_data in phases.items():
        out.append(
            f"========================================\nPHASE: {p_id}\n========================================\n\n")

        allowed_phase = {'identity', 'agents', 'context_override'}
        if 'agents' not in p_data:
            raise ValueError(f"CRITICAL: Phase '{p_id}' missing required field: 'agents'")

        for pk, pv in p_data.items():
            if pk not in allowed_phase:
                print(f"WARNING: Extraneous Phase '{p_id}' field detected -> '{pk}'")

            if pk in ['identity', 'context_override']:
                out.append(f"[{pk.upper()}]\n{pv}\n[END {pk.upper()}]\n\n")

        # --- AGENTS LEVEL ---
        agents = p_data.get('agents', {})
        for a_id, a_data in agents.items():
            out.append(
                f"########################################\n# AGENT: {a_id}\n########################################\n\n")

            allowed_agent = {'identity', 'model_tier', 'model_type', 'context_override', 'tasks'}
            if 'tasks' not in a_data:
                raise ValueError(f"CRITICAL: Agent '{a_id}' missing required field: 'tasks'")

            for ak, av in a_data.items():
                if ak not in allowed_agent:
                    print(f"WARNING: Extraneous Agent '{a_id}' field detected -> '{ak}'")

                if ak in ['identity', 'context_override']:
                    out.append(f"[{ak.upper()}]\n{av}\n[END {ak.upper()}]\n\n")
                elif ak in ['model_tier', 'model_type']:
                    out.append(f"{ak}: {av}\n")

            if 'model_tier' in a_data or 'model_type' in a_data:
                out.append("\n")

            # --- TASKS LEVEL ---
            tasks = a_data.get('tasks', {})
            for t_id, t_data in tasks.items():
                out.append(f"--- TASK: {t_id} ---\n\n")

                # Tasks support Free-Form fields. We process specific ones first for formatting,
                # then dump the rest generically.

                # Known multi-line tags
                multiline_keys = ['instruction', 'context_override', 'result']

                for tk, tv in t_data.items():
                    if tk in multiline_keys:
                        out.append(f"[{tk.upper()}]\n{tv}\n[END {tk.upper()}]\n\n")
                    elif tk == 'terminal_mode':
                        status = tv.get('status', 'failed')
                        msg = tv.get('message_field', '')
                        out.append(f"Terminal Mode:\n- Status: {status}\n- Message Field: {msg}\n\n")
                    elif tk not in ['schema']:
                        # Generic free-form fallback (history_scope, model_tier, model_type, custom fields)
                        # Ensure we stringify booleans/lists appropriately
                        val_str = str(tv).lower() if isinstance(tv, bool) else str(tv)
                        out.append(f"{tk}: {val_str}\n")

                # Handle Returns (Schema vs Image Blob based on model_type)
                m_type = t_data.get('model_type', a_data.get('model_type', 'text'))
                schema = t_data.get('schema', {})

                if m_type == 'img2img':
                    out.append("\nReturns:\n- <IMAGE>\n\n")
                elif schema and schema.get('properties'):
                    props = schema.get('properties', {})
                    reqs = schema.get('required', [])

                    out.append("\nReturns:\n")
                    for prop_k, prop_v in props.items():
                        t = prop_v.get('type', 'STRING')
                        d = prop_v.get('description', '')

                        # Extract ENUMs if they exist
                        enum_vals = prop_v.get('enum')
                        enum_str = f" [ENUM: {', '.join(enum_vals)}]" if enum_vals else ""

                        # Extract Array Item types if they exist
                        items_obj = prop_v.get('items')
                        if t == "ARRAY" and items_obj and 'type' in items_obj:
                            t = f"ARRAY of {items_obj['type']}s"

                        opt = "" if prop_k in reqs else " [Optional]"

                        typ_str = f" ({t})" if t != "STRING" else ""
                        desc_str = f": {d}" if d else ""

                        out.append(f"- {prop_k}{typ_str}{enum_str}{desc_str}{opt}\n")
                    out.append("\n")

    # --- GLOSSARY ---
    glossary = """
========================================
FIELD GLOSSARY
========================================

HIERARCHY FIELDS
----------------
- phases: Top-level groupings of agents representing distinct stages in the EDEE pipeline.
- agents: Entities within a phase with a specific role, model, and set of tasks.
- tasks: Specific executable actions an agent can perform.

INSTRUCTION FIELDS
------------------
- identity: System instructions defining the agent's persona, rules, and core objectives. These are combined (Global + Phase + Agent) before execution.
- instruction: The specific prompt or command for the LLM to execute a particular task.
- context_override: Replaces the default `original_query` context template for a Phase, Agent, or Task. Used to inject different parts of the user input or history.
- result: Used only when `model_tier: "no_model"`. Defines a hardcoded string or JSON structure to return, bypassing the LLM. Supports `${}` variable injection.

CONFIGURATION FIELDS
--------------------
- model_tier: Specifies the speed/cost tier ("fast", "medium", "slow"). If set to `"no_model"`, the task bypasses the API completely.
- model_type: Defines the capability required ("text", "view_img", "img2img"). This automatically triggers image attachment and sets the expected output formats (e.g. "img2img" expects an image blob).
- history_scope: Determines how much previous log context the LLM sees. Options: `"agent"` (sees only its own previous iterations - DEFAULT), `"phase"` (sees all history from current phase), `"global"` (sees entire run history), or `"none"`.
- terminal_mode: If present, instructs the Universal Agent to cleanly halt the pipeline. Contains `status` (e.g., `"failed"`) and `message_field` (the specific JSON key in the AI's response to broadcast to the UI webhook).
- schema: Defines the strict JSON structure the LLM must return via `response_schema`. (Translated in this file into the "Returns:" lists).
"""
    out.append(glossary)

    # Cleanup extraneous newlines created by loop concatenation
    final_text = "".join(out)
    final_text = re.sub(r'\n{3,}(?!--- TASK)', '\n\n', final_text)

    return final_text.strip() + "\n"


# ==========================================
# 3. EXECUTION
# ==========================================

if __name__ == "__main__":
    print("Parsing config.js...")
    config_data = parse_config_to_dict("config.js")

    if config_data:
        print("Validating hierarchy and formatting text...")
        try:
            final_doc = validate_and_format(config_data)

            with open("agent_definitions.txt", "w", encoding="utf-8") as f:
                f.write(final_doc)
            print("\nSuccess! Agent definitions rigidly validated and extracted to agent_definitions.txt")

        except Exception as e:
            print(f"\n{str(e)}")