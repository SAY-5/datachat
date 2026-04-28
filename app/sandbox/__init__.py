"""Sandboxed Python execution.

The runner spawns a fresh subprocess for every code execution and
applies several layers of confinement:

  * RLIMIT_CPU      — kernel-enforced CPU seconds
  * RLIMIT_AS       — virtual-memory cap
  * RLIMIT_FSIZE    — max bytes any single file written
  * RLIMIT_NPROC    — fork limit (defends against fork bombs)
  * Wiped env       — only PATH/HOME/LANG/PYTHONPATH inherited; no
                      proxies, no creds, no AWS_*/OPENAI_*/etc.
  * Confined cwd    — fresh tempdir; the dataset is the only thing
                      mounted in.
  * No shell        — subprocess.run([interp, script]); never shell=True.
  * No network *    — best-effort, see SECURITY note below.

* Network confinement is best-effort because Python's stdlib has no
  portable in-process way to disable the network entirely. Where
  available we use ``unshare(CLONE_NEWNET)``; otherwise the harness
  relies on sys.audit hooks installed in the runner script to refuse
  socket creation. A defense-in-depth deployment runs the subprocess
  inside a one-shot Docker / Firecracker / gVisor VM (see
  docs/DEPLOY.md).
"""

from .runner import ExecResult, SandboxConfig, SandboxRunner

__all__ = ["ExecResult", "SandboxConfig", "SandboxRunner"]
