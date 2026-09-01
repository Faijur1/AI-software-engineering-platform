"""The sandbox boundary, asserted against real containers.

ADR-006 says these properties "are asserted by tests, not assumed", and that is
the entire reason this file exists. Each test tries to *break out* in a specific
way and requires the attempt to fail. A sandbox whose constraints nobody
exercises is a sandbox nobody has.

Needs Docker running, and the ``python:3.11-slim`` image available.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.sandbox import runner
from app.sandbox.runner import SandboxUnavailableError, build_command, is_available, run

pytestmark = pytest.mark.sandbox


@pytest.fixture(autouse=True)
def _require_docker() -> None:
    if not is_available():
        pytest.skip("Docker is not available")


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Path]:
    (tmp_path / "hello.txt").write_text("from the workspace\n", encoding="utf-8")
    yield tmp_path


# --- the command is the policy ---------------------------------------------


def test_every_boundary_flag_is_present() -> None:
    """The docker arguments *are* the security policy.

    Asserted verbatim, without starting a container, so that quietly dropping
    one fails here rather than silently weakening isolation in production.
    """
    command = build_command(
        container_name="test",
        workspace=Path.cwd(),
        image="python:3.11-slim",
        argv=["true"],
        memory="512m",
        cpus="1.0",
    )
    joined = " ".join(command)

    assert "--network none" in joined, "network isolation"
    assert "--memory 512m" in joined and "--memory-swap 512m" in joined, "swap escape"
    assert "--cpus 1.0" in joined
    assert "--pids-limit 256" in joined, "fork bombs"
    assert "--user 65534:65534" in joined, "non-root"
    assert "--read-only" in joined, "immutable root filesystem"
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--rm" in joined, "teardown"


# --- the boundary, exercised for real --------------------------------------


def test_a_command_runs_and_its_output_is_captured(workspace: Path) -> None:
    result = run(["python", "-c", "print('hello from sandbox')"], workspace=workspace)

    assert result.succeeded
    assert result.exit_code == 0
    assert "hello from sandbox" in result.stdout
    assert result.duration_ms > 0


def test_a_failing_command_is_reported_not_raised(workspace: Path) -> None:
    """"Your code failed" is an answer; only a broken sandbox is an error."""
    result = run(["python", "-c", "import sys; sys.exit(3)"], workspace=workspace)

    assert not result.succeeded
    assert result.exit_code == 3
    assert not result.timed_out


def test_stderr_is_captured_separately(workspace: Path) -> None:
    result = run(
        ["python", "-c", "import sys; sys.stderr.write('to stderr')"],
        workspace=workspace,
    )

    assert "to stderr" in result.stderr
    assert "to stderr" not in result.stdout


def test_the_network_is_unreachable(workspace: Path) -> None:
    """The single most important property: exfiltration is impossible."""
    script = (
        "import socket, sys\n"
        "socket.setdefaulttimeout(5)\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53))\n"
        "    print('NETWORK REACHABLE'); sys.exit(0)\n"
        "except OSError as exc:\n"
        "    print('blocked:', type(exc).__name__); sys.exit(7)\n"
    )
    result = run(["python", "-c", script], workspace=workspace)

    assert "NETWORK REACHABLE" not in result.stdout
    assert result.exit_code == 7


def test_dns_does_not_resolve(workspace: Path) -> None:
    script = (
        "import socket, sys\n"
        "try:\n"
        "    socket.gethostbyname('example.com')\n"
        "    print('DNS WORKED'); sys.exit(0)\n"
        "except OSError:\n"
        "    sys.exit(7)\n"
    )
    result = run(["python", "-c", script], workspace=workspace)

    assert "DNS WORKED" not in result.stdout
    assert result.exit_code == 7


def test_the_process_is_not_root(workspace: Path) -> None:
    result = run(["python", "-c", "import os; print(os.getuid())"], workspace=workspace)

    assert result.stdout.strip() == "65534"


def test_the_root_filesystem_is_read_only(workspace: Path) -> None:
    script = (
        "import sys\n"
        "try:\n"
        "    open('/evil.txt', 'w').write('x')\n"
        "    print('WROTE TO ROOT'); sys.exit(0)\n"
        "except OSError:\n"
        "    sys.exit(7)\n"
    )
    result = run(["python", "-c", script], workspace=workspace)

    assert "WROTE TO ROOT" not in result.stdout
    assert result.exit_code == 7


def test_the_workspace_is_readable_and_writable(workspace: Path) -> None:
    """The one hole in the read-only filesystem, and it must work."""
    script = (
        "print(open('/workspace/hello.txt').read().strip())\n"
        "open('/workspace/written.txt', 'w').write('by the sandbox')\n"
    )
    result = run(["python", "-c", script], workspace=workspace)

    assert result.succeeded, result.stderr
    assert "from the workspace" in result.stdout
    # And the write is visible on the host, which is how test output gets back.
    assert (workspace / "written.txt").read_text(encoding="utf-8") == "by the sandbox"


def test_nothing_outside_the_workspace_is_mounted(workspace: Path) -> None:
    """The host filesystem must not be reachable through the mount."""
    script = (
        "import os, sys\n"
        "entries = set(os.listdir('/'))\n"
        "print(sorted(entries))\n"
        "sys.exit(0 if 'workspace' in entries else 9)\n"
    )
    result = run(["python", "-c", script], workspace=workspace)

    assert result.exit_code == 0
    # The host's own project directory is not visible under any name.
    assert "AI-software-engineering-platform" not in result.stdout


def test_a_hanging_command_is_killed_at_the_timeout(workspace: Path) -> None:
    """A timeout that leaves the container running is not a timeout."""
    result = run(
        ["python", "-c", "import time; time.sleep(120)"],
        workspace=workspace,
        timeout_seconds=5,
    )

    assert result.timed_out
    assert result.exit_code == 124
    # Comfortably under the 120s the command asked for.
    assert result.duration_ms < 45_000


def test_the_container_is_gone_after_a_timeout(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guaranteed teardown, including on the path most likely to leak."""
    import subprocess

    killed: list[str] = []
    original = runner._force_remove

    def spy(container_name: str) -> None:
        killed.append(container_name)
        original(container_name)

    monkeypatch.setattr(runner, "_force_remove", spy)

    run(
        ["python", "-c", "import time; time.sleep(120)"],
        workspace=workspace,
        timeout_seconds=5,
    )

    assert killed, "timeout must trigger explicit teardown"
    listed = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={killed[0]}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert killed[0] not in listed.stdout


def test_memory_limits_are_enforced(workspace: Path) -> None:
    """A runaway allocation must be stopped, not swallow the host."""
    script = "x = bytearray(400 * 1024 * 1024)\nprint('ALLOCATED', len(x))\n"
    result = run(
        ["python", "-c", script], workspace=workspace, memory="128m", timeout_seconds=60
    )

    assert "ALLOCATED" not in result.stdout
    assert not result.succeeded


def test_enormous_output_is_truncated_and_says_so(workspace: Path) -> None:
    result = run(
        ["python", "-c", "print('x' * 200000)"], workspace=workspace, timeout_seconds=60
    )

    assert result.truncated
    assert "(output truncated)" in result.stdout
    assert len(result.stdout) < 200_000


def test_a_missing_workspace_is_rejected_before_running(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="workspace"):
        run(["true"], workspace=tmp_path / "does-not-exist")


def test_an_unavailable_docker_refuses_rather_than_degrading(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falling back to host execution is the one thing that must never happen."""
    monkeypatch.setattr(runner, "is_available", lambda: False)

    with pytest.raises(SandboxUnavailableError):
        run(["python", "-c", "print('should never run')"], workspace=workspace)
