import re


def clean_js_string(raw_val):
    """Extracts raw text from a JS string for a strict, literal character comparison."""
    if not raw_val: return ""
    if raw_val.startswith(('"', "'", '`')) and raw_val.endswith(('"', "'", '`')):
        content = raw_val[1:-1]
        if raw_val.startswith('`'):
            # Strip ONLY the structural escapes required by JS, leaving the actual text intact
            if content.startswith('\\\n'):
                content = content[2:]
            elif content.startswith('\\\r\n'):
                content = content[3:]
            elif content.startswith('\n'):
                content = content[1:]
        # Normalize line endings and unescape internal backticks. NO STRIPPING.
        return content.replace('\\`', '`').replace('\r\n', '\n')
    return raw_val.replace('\r\n', '\n')


def extract_text_blocks(txt_file):
    """Scans the text file and extracts IDENTITY and INSTRUCTION blocks exactly as written."""
    blocks = {}
    with open(txt_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    current_phase = current_agent = current_task = None
    in_block = False
    current_tag = None
    buffer = []

    for line in lines:
        line_str = line.strip()

        # --- HIERARCHY TRACKING ---
        if line_str.startswith("PHASE:"):
            current_phase = line_str.split("PHASE:")[1].strip()
            current_agent = current_task = None
        elif line_str.startswith("# AGENT:"):
            current_agent = line_str.split("# AGENT:")[1].strip()
            current_task = None
        elif line_str.startswith("--- TASK:"):
            current_task = line_str.split("--- TASK:")[1].strip().strip("- ")

        # --- BLOCK CAPTURE ---
        elif line_str in ["[IDENTITY]", "[INSTRUCTION]"]:
            in_block = True
            current_tag = line_str[1:-1].lower()  # Extracts 'identity' or 'instruction'
            buffer = []
        elif in_block and line_str == f"[END {current_tag.upper()}]":
            in_block = False
            # Join exactly with newlines. NO STRIPPING.
            val = "\n".join(buffer)

            # Construct the unique tracking key
            if current_tag == 'instruction' and current_task:
                track_key = f"phase_{current_phase}_agent_{current_agent}_task_{current_task}_instruction"
            elif current_tag == 'identity' and current_agent:
                track_key = f"phase_{current_phase}_agent_{current_agent}_identity"
            elif current_tag == 'identity' and current_phase:
                track_key = f"phase_{current_phase}_identity"
            else:
                continue  # Failsafe for orphaned blocks

            blocks[track_key] = val
        elif in_block:
            # Only remove the carriage return, keep trailing spaces and empty lines exact
            buffer.append(line.rstrip('\r\n'))

    return blocks


def scan_js_for_blocks(js_file):
    """Scans config.js and returns a map of target keys to their exact string index spans."""
    with open(js_file, 'r', encoding='utf-8') as f:
        raw_js = f.read()

    lines = raw_js.splitlines(keepends=True)

    spans = {}
    current_phase = current_agent = current_task = None

    char_pos = 0
    in_multiline = False
    multiline_start_pos = 0
    multiline_key = None

    for line in lines:
        stripped = line.strip()

        # --- MULTILINE CONTINUATION ---
        if in_multiline:
            idx = 0
            while idx < len(line):
                if line[idx] == '\\':
                    idx += 2
                    continue
                if line[idx] == '`':
                    end_pos = char_pos + idx + 1
                    spans[multiline_key] = (multiline_start_pos, end_pos)
                    in_multiline = False
                    break
                idx += 1
            char_pos += len(line)
            continue

        # --- HIERARCHY TRACKING ---
        leading_ws = re.match(r'^[ \t]*', line).group(0)
        indent = len(leading_ws.expandtabs(4))

        m_key = re.match(r'^["\'`]?([a-zA-Z0-9_\-]+)["\'`]?:\s*\{', stripped)
        if m_key:
            key = m_key.group(1)
            if indent == 4:
                current_phase = key
                current_agent = current_task = None
            elif indent == 8:
                current_agent = key
                current_task = None
            elif indent == 12:
                current_task = key

        # --- SPAN CAPTURE (Identity & Instruction) ---
        is_identity = bool(re.match(r'^["\']?identity["\']?\s*:', stripped))
        is_instruction = bool(re.match(r'^["\']?instruction["\']?\s*:', stripped))

        if is_identity or is_instruction:
            if (is_identity and indent in [6, 10]) or (is_instruction and indent == 14):
                colon_idx = line.find(':')
                val_str = line[colon_idx + 1:].lstrip()

                val_start_in_line = len(line) - len(val_str)
                absolute_val_start = char_pos + val_start_in_line

                # Construct tracking key
                if is_instruction:
                    track_key = f"phase_{current_phase}_agent_{current_agent}_task_{current_task}_instruction"
                elif is_identity and current_agent:
                    track_key = f"phase_{current_phase}_agent_{current_agent}_identity"
                else:
                    track_key = f"phase_{current_phase}_identity"

                if val_str.startswith('`'):
                    idx = 1
                    closed_inline = False
                    while idx < len(val_str):
                        if val_str[idx] == '\\':
                            idx += 2
                            continue
                        if val_str[idx] == '`':
                            end_pos = absolute_val_start + idx + 1
                            spans[track_key] = (absolute_val_start, end_pos)
                            closed_inline = True
                            break
                        idx += 1

                    if not closed_inline:
                        in_multiline = True
                        multiline_start_pos = absolute_val_start
                        multiline_key = track_key

                elif val_str.startswith(('"', "'")):
                    quote = val_str[0]
                    idx = 1
                    while idx < len(val_str):
                        if val_str[idx] == '\\':
                            idx += 2
                            continue
                        if val_str[idx] == quote:
                            end_pos = absolute_val_start + idx + 1
                            spans[track_key] = (absolute_val_start, end_pos)
                            break
                        idx += 1

        char_pos += len(line)

    return spans, raw_js


def update_blocks(js_file="config.js", txt_file="agent_definitions.txt", output_file="config_updated.js"):
    print("Reading text blocks...")
    txt_blocks = extract_text_blocks(txt_file)

    print("Locating string spans in JS...")
    spans, raw_js = scan_js_for_blocks(js_file)

    edits = []
    updated_count = 0
    skipped_count = 0

    print("\n--- SYNCHRONIZING ---")
    for key, new_text in txt_blocks.items():
        if key in spans:
            start, end = spans[key]
            old_raw = raw_js[start:end]
            old_text = clean_js_string(old_raw)

            new_text_clean = new_text.replace('\r\n', '\n')

            if old_text != new_text_clean:
                # Force the \` FORMAT regardless of what the old string used
                safe_new_text = new_text_clean.replace('`', '\\`')
                new_block = f"`\\\n{safe_new_text}`"
                edits.append((start, end, new_block))
                print(f"[UPDATED] {key}")
                updated_count += 1
            else:
                print(f"[SKIPPED] {key} (No changes detected)")
                skipped_count += 1
        else:
            print(f"[WARNING] Could not find JS location for '{key}'")

    # Sort edits backwards so string offsets don't shift during replacement
    edits.sort(key=lambda x: x[0], reverse=True)
    new_js = raw_js
    for start, end, text in edits:
        new_js = new_js[:start] + text + new_js[end:]

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(new_js)

    print("\n=== SYNC COMPLETE ===")
    print(f"Updated : {updated_count}")
    print(f"Skipped : {skipped_count}")
    print(f"Everything else left completely untouched.")
    print(f"Output written to {output_file}")


if __name__ == "__main__":
    update_blocks()