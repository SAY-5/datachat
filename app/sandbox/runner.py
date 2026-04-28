"""Subprocess sandbox runner."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Path to the in-tree harness script (sandbox.harness) — we resolve it
# at import time so callers don't have to know where it lives.
_HARNESS = Path(__file__).resolve().parent / "harness.py"


@dataclass
class SandboxConfig:
    cpu_seconds: int = 5
    memory_bytes: int = 512 * 1024 * 1024
    file_size_bytes: int = 16 * 1024 * 1024
    nproc: int = 16
    wall_seconds: float = 10.0
    output_truncate_bytes: int = 8192
    interpreter: str = field(default_factory=lambda: sys.executable)
    extra_env: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecResult:
    ok: bool
    figure: dict[str, Any] | None
    result_repr: str | None
    stdout: str
    stderr: str
    elapsed_ms: int
    peak_mem_bytes: int
    exit_code: int
    error_class: str | None
    error_message: str | None


class SandboxRunner:
    """Execute LLM-generated code against a CSV dataset.

    Each call spawns a fresh subprocess in a fresh tempdir. The
    dataset is symlinked into the tempdir read-only; the user code
    runs with cwd = tempdir, with the env stripped to a minimal set,
    and with kernel-level resource limits via setrlimit.
    """

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()

    async def run(self, code: str, dataset_path: str | None = None) -> ExecResult:
        with tempfile.TemporaryDirectory(prefix="datachat-sb-") as tmp:
            tmp_path = Path(tmp)
            script_path = tmp_path / "main.py"
            script_path.write_text(code, encoding="utf-8")

            dataset_in_sandbox: str | None = None
            if dataset_path:
                src = Path(dataset_path).resolve()
                if not src.exists():
                    return ExecResult(
                        ok=False, figure=None, result_repr=None,
                        stdout="", stderr=f"dataset not found: {dataset_path}",
                        elapsed_ms=0, peak_mem_bytes=0, exit_code=2,
                        error_class="DatasetMissing", error_message=str(src),
                    )
                # Copy in instead of symlinking so that even if the user
                # code somehow escapes the tempdir, the original isn't
                # writable through this path. Cost is one bounded I/O.
                dataset_in_sandbox = str(tmp_path / "dataset.csv")
                shutil.copyfile(src, dataset_in_sandbox)

            payload_path = tmp_path / "_result.json"
            env = self._minimal_env(tmp_path)
            argv = [
                self.config.interpreter,
                str(_HARNESS),
                str(script_path),
                str(payload_path),
                dataset_in_sandbox or "",
            ]
            preexec = self._make_preexec()
            started = time.time()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(tmp_path),
                    env=env,
                    preexec_fn=preexec,
                )
            except OSError as e:
                return ExecResult(
                    ok=False, figure=None, result_repr=None,
                    stdout="", stderr=f"failed to spawn sandbox: {e}",
                    elapsed_ms=0, peak_mem_bytes=0, exit_code=-1,
                    error_class="SpawnFailed", error_message=str(e),
                )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self.config.wall_seconds,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return ExecResult(
                    ok=False, figure=None, result_repr=None,
                    stdout="", stderr=f"wall timeout {self.config.wall_seconds}s",
                    elapsed_ms=int((time.time() - started) * 1000),
                    peak_mem_bytes=0, exit_code=-1,
                    error_class="WallTimeout", error_message=None,
                )

            elapsed_ms = int((time.time() - started) * 1000)
            stdout = self._truncate(stdout_b)
            stderr = self._truncate(stderr_b)
            payload: dict[str, Any] = {}
            if payload_path.exists():
                try:
                    payload = json.loads(payload_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    payload = {}

            ok = proc.returncode == 0 and payload.get("ok", False)
            return ExecResult(
                ok=ok,
                figure=payload.get("figure"),
                result_repr=payload.get("result_repr"),
                stdout=stdout,
                stderr=stderr,
                elapsed_ms=elapsed_ms,
                peak_mem_bytes=int(payload.get("peak_mem_bytes", 0)),
                exit_code=proc.returncode if proc.returncode is not None else -1,
                error_class=payload.get("error_class"),
                error_message=payload.get("error_message"),
            )

    # ----- internals ------------------------------------------------

    def _minimal_env(self, tmp_path: Path) -> dict[str, str]:
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": str(tmp_path),
            # Keep PYTHONPATH so the harness can import its sibling
            # bootstrap script — but DO NOT inherit sys.path additions
            # from the host process.
            "PYTHONPATH": str(_HARNESS.parent),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        env.update(self.config.extra_env)
        return env

    def _make_preexec(self):
        # preexec_fn runs in the child after fork() but before exec().
        # We use it to install the rlimits. On Windows this whole
        # branch is a no-op (preexec_fn is unsupported); the
        # subprocess inherits an unsandboxed environment and the
        # caller is expected to run inside a container.
        if os.name != "posix":
            return None

        cfg = self.config

        def _apply():
            import resource

            # All limits are best-effort. macOS in particular rejects
            # several rlimits (RLIMIT_AS for non-root, RLIMIT_NPROC, etc.).
            # If the kernel says no, we keep going — the audit hook,
            # minimal env, and confined cwd are still in force, and the
            # parent-side wall-clock kill still bounds runtime.
            for name, value in (
                ("RLIMIT_CPU", (cfg.cpu_seconds, cfg.cpu_seconds)),
                ("RLIMIT_AS", (cfg.memory_bytes, cfg.memory_bytes)),
                ("RLIMIT_FSIZE", (cfg.file_size_bytes, cfg.file_size_bytes)),
                ("RLIMIT_NPROC", (cfg.nproc, cfg.nproc)),
                ("RLIMIT_CORE", (0, 0)),
            ):
                limit = getattr(resource, name, None)
                if limit is None:
                    continue
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(limit, value)

        return _apply

    def _truncate(self, b: bytes) -> str:
        if len(b) <= self.config.output_truncate_bytes:
            return b.decode("utf-8", "replace")
        head = b[: self.config.output_truncate_bytes].decode("utf-8", "replace")
        return head + f"\n…[truncated {len(b) - self.config.output_truncate_bytes} bytes]"
