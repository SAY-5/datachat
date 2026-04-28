"""Harness that runs *inside* the sandboxed subprocess.

Responsibilities:
  1. Install a sys.audit hook that refuses dangerous operations
     (importing socket/subprocess, calling os.system/os.execv,
     exec()/eval()/compile() on user-controlled strings).
  2. Load the dataset CSV (if any) into a pandas DataFrame called
     ``df`` and expose it to the user code's globals.
  3. Compile + execute the user code in that restricted globals dict.
  4. Capture the resulting ``result`` and ``fig`` values.
  5. Serialize the result + Plotly figure JSON + stdout/stderr to a
     known JSON file the parent reads back.

The harness runs as a normal Python script with the parent process's
RLIMITs already in effect; nothing here can extend them.
"""

from __future__ import annotations

import io
import json
import resource
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# Modules the user code is allowed to import. The audit hook below
# refuses anything else that would meaningfully reach the host.
_FORBIDDEN_MODULES = {
    "socket", "ssl",
    "subprocess",
    "os",
    "shutil",
    "ctypes",
    "multiprocessing",
    "asyncio",
    "threading",
    "urllib", "urllib2", "urllib3", "http", "httpx", "requests",
    "ftplib", "smtplib", "telnetlib", "poplib",
    "pickle", "shelve", "marshal",
}

# Audit events whose presence indicates a sandbox-relevant action.
_FORBIDDEN_EVENTS = {
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.spawn",
    "os.fork",
    "os.forkpty",
    "socket.connect",
    "socket.bind",
    "import.builtins.compile",  # synthetic name; we also gate `compile`
}


class SandboxViolation(Exception):
    """Raised by the audit hook when user code tries to do something forbidden."""


def _install_audit_hook() -> None:
    def hook(event: str, args: tuple) -> None:
        if event in _FORBIDDEN_EVENTS:
            raise SandboxViolation(f"sandbox blocked event: {event}")
        if event == "import":
            # args[0] is the module name being imported.
            mod = args[0] if args else ""
            top = mod.split(".", 1)[0]
            if top in _FORBIDDEN_MODULES:
                raise SandboxViolation(f"sandbox blocked import: {mod}")

    sys.addaudithook(hook)


def _prewarm() -> dict[str, object]:
    """Pre-import the allowed surface BEFORE the audit hook goes live.

    Pandas, numpy, and plotly transitively import os, threading, and a
    handful of other modules we forbid for user code. Doing those imports
    here means they hit Python's module cache during user code execution
    — `import pandas as pd` is then a cache lookup, and the audit hook
    only fires for new imports the user is initiating themselves.
    """
    import numpy as np  # noqa: F401
    import pandas as pd
    import plotly  # noqa: F401
    import plotly.express  # noqa: F401
    import plotly.graph_objects  # noqa: F401
    return {"pd": pd}


def _load_dataset(path: str, pd: object) -> object:
    if not path:
        return None
    return pd.read_csv(path)  # type: ignore[attr-defined]


def _serialize_figure(fig: object) -> dict | None:
    if fig is None:
        return None
    # plotly.graph_objects.Figure has .to_dict() that returns a JSON-
    # serializable view; we additionally pass through json.loads(json.dumps(...))
    # to flatten numpy types into plain Python.
    try:
        d = fig.to_dict()
    except AttributeError:
        # Already a dict-shaped figure.
        d = dict(fig)
    return json.loads(json.dumps(d, default=_default_json))


def _default_json(o: object) -> object:
    # numpy/pandas types fall through here.
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def _peak_mem_bytes() -> int:
    ru = resource.getrusage(resource.RUSAGE_SELF)
    # Linux: ru_maxrss in KB; macOS: in bytes. Heuristic: if huge,
    # assume bytes; otherwise treat as KB.
    if ru.ru_maxrss > 10 * 1024 * 1024:
        return int(ru.ru_maxrss)
    return int(ru.ru_maxrss * 1024)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: harness.py <user_script> <result_path> <dataset_or_empty>",
              file=sys.stderr)
        return 2
    user_script = Path(argv[1])
    result_path = Path(argv[2])
    dataset_path = argv[3]

    # 1. Pre-import the allowed library surface so transitive imports
    #    of os/threading/etc. don't trip the audit hook later.
    warm = _prewarm()
    # 2. Load the dataset. read_csv lazy-imports a handful of parsers;
    #    triggering it here keeps those out of the user's call stack.
    df = _load_dataset(dataset_path, warm["pd"])
    # 3. NOW install the audit hook. Any new import from user code is
    #    on its own.
    _install_audit_hook()

    code = user_script.read_text(encoding="utf-8")

    # Per the prompt, user code should assign `result` and optionally
    # `fig`. We expose `df` (the dataset) and a minimal helper module
    # surface; pandas/numpy/plotly are imported on demand by the user.
    g: dict[str, object] = {"__name__": "__main__", "df": df}
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    payload: dict[str, object] = {"ok": False}

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            compiled = compile(code, str(user_script), "exec")
            exec(compiled, g, g)  # noqa: S102 — sandbox by design
        result = g.get("result")
        fig = g.get("fig")
        payload["ok"] = True
        payload["result_repr"] = _safe_repr(result)
        payload["figure"] = _serialize_figure(fig)
    except SandboxViolation as v:
        payload["ok"] = False
        payload["error_class"] = "SandboxViolation"
        payload["error_message"] = str(v)
    except SystemExit as e:
        payload["ok"] = False
        payload["error_class"] = "SystemExit"
        payload["error_message"] = f"exit code: {e.code}"
    except KeyboardInterrupt:
        payload["ok"] = False
        payload["error_class"] = "KeyboardInterrupt"
        payload["error_message"] = "wall timeout or signal"
    except BaseException as e:  # noqa: BLE001
        payload["ok"] = False
        payload["error_class"] = type(e).__name__
        payload["error_message"] = str(e)
        # Echo the traceback to stderr so the parent surfaces it.
        traceback.print_exc(file=stderr_buf)

    payload["peak_mem_bytes"] = _peak_mem_bytes()
    # Flush captured streams to the real stdout/stderr so the parent
    # sees them (it reads the subprocess pipes too).
    sys.__stdout__.write(stdout_buf.getvalue())
    sys.__stderr__.write(stderr_buf.getvalue())
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    return 0 if payload.get("ok") else 1


def _safe_repr(o: object) -> str:
    try:
        s = repr(o)
    except Exception as e:  # noqa: BLE001
        return f"<unrepresentable: {e}>"
    return s if len(s) <= 1024 else s[:1024] + "…"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
