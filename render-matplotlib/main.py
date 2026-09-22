"""
EDEE render-matplotlib — sandboxed matplotlib renderer (Google Cloud Function).

Executes the LLM-written scaffolding script and returns a PNG. Applies the same
post-processing safeguards the agents are told about in config.js's
EXECUTION_CONTRACT (strip axes, equal aspect, crop) so coder mistakes can't
ruin the render, and returns a readable Python traceback on failure so the
review_manager / error_expert agents can fix the code.

REQUEST  (POST, JSON)
    {
      "code":        "<python source>",           required
      "square":      false,   force a 1:1 output canvas (see FORCE_SQUARE_CANVAS_DEFAULT)
      "keep_axes":   false,   keep axes/ticks/grids (graph problems)
      "dpi":         150,     50..300
      "transparent": false    transparent PNG background
    }
RESPONSE
    200  image/png
    400  text/plain   bad request, or the script uses a forbidden module / builtin
    500  text/plain   "Error executing code: <Type>: <message>\n  line N: <source>..."
    GET  /            {"status": "ok", ...}  health check

The error text is consumed verbatim by the n8n "cfg57" node (→ error_injector →
review_manager.troubleshoot), so keep it plain text and keep the leading
"Error executing code:" prefix.
"""
import ast
import builtins
import io
import json
import traceback

import matplotlib
matplotlib.use("Agg")  # headless — must run before pyplot is imported
import matplotlib.pyplot as plt

try:  # the framework (and flask) are only present in the deployed function
    import functions_framework
except ImportError:  # pragma: no cover — local testing via render_png()
    functions_framework = None


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Request field "square" defaults to this.
# False: output is ~4:3 — bbox_inches='tight' trims to the AXES BOX (not to the drawn
#        content), and set_aspect('equal', adjustable='datalim') pins that box at
#        matplotlib's default proportions (4.96 x 3.696 in = 1.342), padding the data
#        limits instead. Shape is therefore constant whatever the scene is.
# True:  force a 1:1 output by squaring the axes box itself. Data still renders at true
#        'equal' aspect, so non-square scenes gain whitespace bands rather than distorting.
FORCE_SQUARE_CANVAS_DEFAULT = False

DEFAULT_DPI = 150
MIN_DPI, MAX_DPI = 50, 300
MAX_FIG_INCHES = 24          # cap either figure dimension (a 24in @ 300dpi side is 7200px)
MAX_CODE_CHARS = 200_000
SCRIPT_NAME = "<scaffolding>"  # filename stamped on compiled code → recognisable traceback frames

# The generated script is supposed to touch only matplotlib / numpy / the maths-ish
# parts of the standard library. Everything with filesystem, network, process or
# interpreter reach is refused up front — the function runs with GCP credentials.
BLOCKED_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "glob", "tempfile",
    "requests", "urllib", "urllib3", "http", "ftplib", "smtplib", "imaplib", "poplib",
    "telnetlib", "xmlrpc", "webbrowser", "importlib", "ctypes", "multiprocessing",
    "threading", "concurrent", "asyncio", "signal", "builtins", "pickle", "marshal",
    "shelve", "dbm", "sqlite3", "code", "codeop", "pty", "tty", "termios", "resource",
    "inspect", "gc", "atexit", "faulthandler", "runpy", "zipimport", "pkgutil",
    "google", "flask", "functions_framework", "main",
}
BLOCKED_BUILTINS = {"open", "exec", "eval", "compile", "input", "breakpoint", "exit", "quit", "help"}
BLOCKED_ATTRS = {"__subclasses__", "__globals__", "__builtins__", "__loader__", "__code__",
                 "__mro__", "__bases__", "__import__"}


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------

class ForbiddenCode(Exception):
    """The script uses something the render sandbox does not allow."""


def _static_check(code: str) -> None:
    """AST pass: refuse blocked imports, builtins and dunder attributes before running."""
    tree = ast.parse(code, filename=SCRIPT_NAME)  # SyntaxError propagates to the caller
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BLOCKED_MODULES:
                    raise ForbiddenCode(f"line {node.lineno}: import of '{alias.name}' is not allowed in the render sandbox")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level == 0 and root in BLOCKED_MODULES:
                raise ForbiddenCode(f"line {node.lineno}: import from '{node.module}' is not allowed in the render sandbox")
        elif isinstance(node, ast.Name) and node.id in BLOCKED_BUILTINS | {"__import__"}:
            raise ForbiddenCode(f"line {node.lineno}: '{node.id}()' is not available in the render sandbox")
        elif isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRS:
            raise ForbiddenCode(f"line {node.lineno}: attribute '{node.attr}' is not allowed in the render sandbox")


_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    # Runtime backstop for the static check (covers imports the AST pass can't see,
    # e.g. built from strings). Only the sandbox namespace routes through here —
    # matplotlib's own internal imports resolve against matplotlib's globals.
    if level == 0 and name.split(".")[0] in BLOCKED_MODULES:
        raise ForbiddenCode(f"import of '{name}' is not allowed in the render sandbox")
    return _real_import(name, globals, locals, fromlist, level)


def _sandbox_builtins() -> dict:
    safe = {k: v for k, v in vars(builtins).items() if k not in BLOCKED_BUILTINS}
    safe["__import__"] = _guarded_import
    return safe


def _format_error(exc: BaseException, code: str) -> str:
    """Compact, line-annotated error for the reviewer agents (no framework frames)."""
    src_lines = code.splitlines()

    def src(n):
        return src_lines[n - 1].strip() if 0 < n <= len(src_lines) else ""

    head = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, SyntaxError):
        line = exc.lineno or 0
        text = (exc.text or src(line)).rstrip()
        return f"{head}\n  line {line}: {text}"

    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == SCRIPT_NAME]
    detail = [f"  line {f.lineno}{'' if f.name == '<module>' else ' in ' + f.name}(): {src(f.lineno)}"
              if f.name != "<module>" else f"  line {f.lineno}: {src(f.lineno)}"
              for f in frames]
    return "\n".join([head] + detail)


# ---------------------------------------------------------------------------
# Core renderer (pure function — used by the HTTP handler and by local tests)
# ---------------------------------------------------------------------------

def render_png(code: str, *, square: bool = FORCE_SQUARE_CANVAS_DEFAULT, keep_axes: bool = False,
               dpi: int = DEFAULT_DPI, transparent: bool = False) -> bytes:
    """Execute `code` and return PNG bytes. Raises ForbiddenCode, SyntaxError, or whatever
    the script raised; the HTTP layer turns those into text responses."""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("No code provided")
    if len(code) > MAX_CODE_CHARS:
        raise ValueError(f"Script too large ({len(code)} chars; limit {MAX_CODE_CHARS})")
    dpi = max(MIN_DPI, min(MAX_DPI, int(dpi or DEFAULT_DPI)))

    _static_check(code)
    compiled = compile(code, SCRIPT_NAME, "exec")

    # Fresh interpreter-ish state per request: no figures, no rcParams or module-level
    # leftovers from the previous job leaking into this one.
    plt.close("all")
    plt.rcdefaults()

    # ONE namespace for globals and locals. The old exec(code, globals(), local_scope)
    # split meant helper functions could not see the script's own top-level imports
    # or variables (functions close over globals, and those were main.py's) — hence
    # the "SCOPE RESTRICTION" the coder used to be warned about. A single dict makes
    # the script behave like a normal module.
    namespace = {"__name__": "__scaffolding__", "__builtins__": _sandbox_builtins()}
    try:
        exec(compiled, namespace)

        # --- SYSTEM POST-PROCESSING SAFEGUARDS -------------------------------------
        fig = plt.gcf()
        axes = fig.get_axes() or [plt.gca()]

        drew_something = any(ax.has_data() or ax.texts or ax.patches for ax in axes)
        if not drew_something:
            raise RuntimeError("The script ran but drew nothing (no shapes, lines, images or text on any axes).")

        for ax in axes:
            is_3d = hasattr(ax, "get_zaxis")
            if not keep_axes:
                ax.axis("off")  # strip axes, grids, borders and ticks
            # NO margin call here. The old ax.margins(0.05) was a verified no-op: the
            # autoscale + set_aspect('equal', adjustable='datalim') pair below recomputes
            # the data limits and discards any margin padding, so it protected nothing
            # (byte-identical renders with and without it, including strokes drawn exactly
            # on the limit). Whitespace we actually want belongs in postprocessing —
            # padding before image gen wastes resolution and pays for blank pixels.
            try:
                if is_3d:
                    ax.set_box_aspect([1, 1, 1])
                else:
                    ax.autoscale(enable=True, tight=False)
                    ax.set_aspect("equal", adjustable="datalim")
            except ValueError:
                pass  # empty/corrupt axis — leave it be
            if square and not is_3d:
                ax.set_box_aspect(1)

        w, h = fig.get_size_inches()
        if w > MAX_FIG_INCHES or h > MAX_FIG_INCHES:
            scale = MAX_FIG_INCHES / max(w, h)
            fig.set_size_inches(w * scale, h * scale)

        buf = io.BytesIO()
        # pad_inches=0: no whitespace border is added around the crop. Any padding we
        # want belongs in postprocessing — paying an image model for blank pixels wastes
        # both resolution and money.
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0,
                    transparent=transparent, facecolor="none" if transparent else "white")
        return buf.getvalue()
    finally:
        plt.close("all")


# ---------------------------------------------------------------------------
# HTTP entry point (Cloud Function target: render_plot)
# ---------------------------------------------------------------------------

def _as_bool(v, default=False):
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def render_plot(request):
    from flask import Response  # flask ships with functions-framework

    if request.method == "GET":
        return Response(json.dumps({
            "status": "ok", "service": "edee-render-matplotlib",
            "matplotlib": matplotlib.__version__,
            "options": ["code", "square", "keep_axes", "dpi", "transparent"],
        }), mimetype="application/json")

    body = request.get_json(silent=True) or {}
    code = body.get("code")
    if not code:
        return Response("No code provided", status=400, mimetype="text/plain")

    try:
        png = render_png(
            code,
            square=_as_bool(body.get("square"), FORCE_SQUARE_CANVAS_DEFAULT),
            keep_axes=_as_bool(body.get("keep_axes")),
            dpi=body.get("dpi") or DEFAULT_DPI,
            transparent=_as_bool(body.get("transparent")),
        )
    except (ForbiddenCode, ValueError) as e:
        return Response(f"Error executing code: {type(e).__name__}: {e}", status=400, mimetype="text/plain")
    except Exception as e:  # noqa: BLE001 — anything the script raised goes back to the reviewer
        return Response("Error executing code: " + _format_error(e, code), status=500, mimetype="text/plain")

    return Response(png, mimetype="image/png")


if functions_framework is not None:
    render_plot = functions_framework.http(render_plot)
