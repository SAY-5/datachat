"""Sandbox enforcement tests — the security-critical bits."""

from __future__ import annotations

import sys

import pytest

from app.sandbox import SandboxConfig, SandboxRunner


@pytest.mark.asyncio
async def test_simple_pandas_returns_result():
    runner = SandboxRunner(SandboxConfig(wall_seconds=10.0))
    code = (
        "import pandas as pd\n"
        "result = pd.DataFrame({'a': [1, 2, 3]}).sum().to_dict()\n"
    )
    res = await runner.run(code)
    assert res.ok, res.stderr
    # 1 + 2 + 3 = 6
    assert "6" in (res.result_repr or "")


@pytest.mark.asyncio
async def test_dataset_is_loaded_as_df():
    runner = SandboxRunner(SandboxConfig(wall_seconds=10.0))
    code = "result = {'rows': len(df), 'cols': list(df.columns)}\n"
    # tiny dataset on the fly
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write("a,b\n1,2\n3,4\n5,6\n")
        path = f.name
    try:
        res = await runner.run(code, path)
    finally:
        Path(path).unlink(missing_ok=True)
    assert res.ok, res.stderr
    assert "rows" in (res.result_repr or "")
    assert "3" in (res.result_repr or "")


@pytest.mark.asyncio
async def test_blocks_socket_import():
    runner = SandboxRunner(SandboxConfig(wall_seconds=5.0))
    res = await runner.run("import socket\nresult = socket.gethostbyname('example.com')\n")
    assert not res.ok
    assert (res.error_class or "").endswith("SandboxViolation")


@pytest.mark.asyncio
async def test_blocks_subprocess_import():
    runner = SandboxRunner(SandboxConfig(wall_seconds=5.0))
    res = await runner.run("import subprocess\nresult = subprocess.run(['ls'])\n")
    assert not res.ok
    assert "SandboxViolation" in (res.error_class or "")


@pytest.mark.asyncio
async def test_blocks_os_system():
    runner = SandboxRunner(SandboxConfig(wall_seconds=5.0))
    res = await runner.run("import os\nos.system('echo pwn')\nresult = 1\n")
    assert not res.ok
    assert (res.error_class or "") in {
        "SandboxViolation", "ImportError", "ModuleNotFoundError",
    }


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32",
                    reason="RLIMITs are POSIX-only; sandbox documents this")
async def test_cpu_timeout_kills_busy_loop():
    runner = SandboxRunner(SandboxConfig(cpu_seconds=1, wall_seconds=8.0))
    code = "x = 0\nwhile True:\n    x += 1\n"
    res = await runner.run(code)
    assert not res.ok
    # The kernel SIGXCPU's the process — it shows up as exit_code != 0.
    assert res.exit_code != 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="RLIMIT_AS is only enforced for non-root on Linux; "
           "macOS rejects it. Sandbox docs the layered defenses.",
)
async def test_memory_cap_kills_giant_alloc():
    runner = SandboxRunner(SandboxConfig(
        memory_bytes=64 * 1024 * 1024,  # 64 MB
        wall_seconds=8.0,
    ))
    # Try to allocate 256 MB.
    code = "result = bytearray(256 * 1024 * 1024)\n"
    res = await runner.run(code)
    assert not res.ok


@pytest.mark.asyncio
async def test_wall_timeout_kills_hung_process():
    runner = SandboxRunner(SandboxConfig(wall_seconds=1.0))
    # No `import time` allowed by the audit hook? time is fine, only the
    # forbidden modules are blocked.
    code = "import time\ntime.sleep(60)\n"
    res = await runner.run(code)
    assert not res.ok
    assert res.error_class == "WallTimeout"


@pytest.mark.asyncio
async def test_plotly_figure_serialises():
    runner = SandboxRunner(SandboxConfig(wall_seconds=10.0))
    code = (
        "import plotly.graph_objects as go\n"
        "fig = go.Figure(go.Bar(x=[1, 2, 3], y=[4, 5, 6]))\n"
        "result = 'ok'\n"
    )
    res = await runner.run(code)
    assert res.ok, res.stderr
    assert res.figure is not None
    assert res.figure.get("data")[0]["type"] == "bar"


@pytest.mark.asyncio
async def test_dataset_missing_returns_clean_error():
    runner = SandboxRunner()
    res = await runner.run("result = 1\n", "/no/such/file.csv")
    assert not res.ok
    assert res.error_class == "DatasetMissing"


@pytest.mark.asyncio
async def test_user_code_exception_surfaces_class():
    runner = SandboxRunner(SandboxConfig(wall_seconds=5.0))
    res = await runner.run("result = 1 / 0\n")
    assert not res.ok
    assert res.error_class == "ZeroDivisionError"
