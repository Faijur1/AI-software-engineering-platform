"""Execute untrusted code inside a disposable Docker container.

This is the hard security boundary from ADR-006, not a convenience wrapper.
Everything it runs is untrusted by construction: repository test suites the
user did not write, and patches an LLM generated. Running any of it in the
worker process would hand it the database credentials.

Every property below is enforced here and asserted by tests, because a boundary
nobody checks is a boundary nobody has:

- CPU and memory limits
- a hard wall-clock timeout that kills the container
- **no network** (``--network none``)
- a non-root user
- a read-only root filesystem, with one writable workspace mount
- captured stdout, stderr and exit code
- guaranteed teardown, including on timeout and on error

The Docker CLI is driven through ``subprocess`` rather than the SDK. The
arguments are the security policy, so they are written where they can be read
in one place and asserted verbatim in a test; an SDK would bury them in keyword
arguments and version-dependent defaults.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Built from docker/sandbox.Dockerfile. It exists because the container runs
# with --network none, so nothing can be installed at run time -- whatever a
# test suite needs is baked in at build time, where the contents are
# reviewable. This is the "pre-built image" mitigation ADR-006 names.
#
# A repository needing tooling that is not in it cannot be tested in Stage 1.
# That is a real limit, and the honest response is to say so rather than to
# relax the network constraint.
DEFAULT_IMAGE: Final = "aisep-sandbox:latest"

# Deliberately conservative. A runaway test suite must not take down the host
# it is running on.
DEFAULT_MEMORY: Final = "512m"
DEFAULT_CPUS: Final = "1.0"
DEFAULT_TIMEOUT_SECONDS: Final = 120

# The one writable path inside the container.
WORKSPACE: Final = "/workspace"
# nobody:nogroup. Present in Debian-based images and owns nothing.
NON_ROOT_USER: Final = "65534:65534"
# How long to wait for a killed container to actually disappear. Removal is
# asynchronous, so teardown is only complete once the daemon stops listing it.
TEARDOWN_WAIT_SECONDS: Final = 15.0

# Output beyond this is truncated. A test suite that prints a megabyte of
# failures should not be stored in full or shown to a model.
MAX_OUTPUT_CHARS: Final = 20_000


class SandboxError(AppError):
    """The sandbox could not run. Distinct from the command failing inside it."""

    status_code = 500
    code = "internal_error"


class SandboxUnavailableError(SandboxError):
    """Docker is not usable, so nothing may be executed at all.

    Deliberately fatal rather than degrading: falling back to running untrusted
    code on the host is the one thing that must never happen (ADR-006).
    """


@dataclass(slots=True)
class SandboxResult:
    """What actually happened. Every field is observed, never inferred."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    # True when output was cut at MAX_OUTPUT_CHARS, so a reader is never shown
    # a truncated log that looks complete.
    truncated: bool = False
    command: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def is_available() -> bool:
    """Whether Docker is present and responding."""
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def image_exists(image: str) -> bool:
    """Whether ``image`` is present locally.

    Checked before running rather than letting docker fail: with no network the
    image cannot be pulled, so a missing one is a setup step the user has to
    take, and saying which command to run is more useful than a pull error.
    """
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def build_command(
    *,
    container_name: str,
    workspace: Path,
    image: str,
    argv: list[str],
    memory: str,
    cpus: str,
) -> list[str]:
    """Build the ``docker run`` argument list.

    Separate from execution so a test can assert the exact security flags
    without starting a container. Every flag here is a boundary property from
    ADR-006; removing one silently weakens the sandbox, which is precisely the
    kind of change a test should fail on.
    """
    return [
        "docker", "run",
        "--rm",                          # teardown even if we lose track of it
        "--name", container_name,
        "--network", "none",             # no network, at all
        "--memory", memory,
        "--memory-swap", memory,         # or the limit is escapable via swap
        "--cpus", cpus,
        "--pids-limit", "256",           # no fork bombs
        "--user", NON_ROOT_USER,         # never root
        "--read-only",                   # immutable root filesystem
        "--cap-drop", "ALL",             # no capabilities
        "--security-opt", "no-new-privileges",
        # The single writable location, and a small tmpfs because many tools
        # refuse to start without a writable /tmp.
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--volume", f"{workspace.resolve()}:{WORKSPACE}:rw",
        "--workdir", WORKSPACE,
        image,
        *argv,
    ]



def _grant_workspace_access(workspace: Path) -> None:
    """Let the sandbox user write to the workspace.

    The container runs as uid 65534, while the workspace is created by whatever
    user runs the application. On Linux that ownership is real, so 0700 or 0755
    temp directories leave the sandbox unable to write: patch application could
    not create its applier script, and a test run could not write output. On
    Docker Desktop for Windows every bind mount appears world-writable, which
    hides the problem entirely -- the sandbox tests passed on Windows and failed
    on the first Linux CI run.

    The alternative was to run as the invoking user's uid instead. That is the
    more common fix and it is rejected here: it makes the container's privileges
    depend on how the application happens to be deployed, and it runs untrusted
    code as root whenever the application itself runs as root, which in a
    container is the default. A fixed unprivileged uid is worth keeping.

    Widening the mode does not widen exposure. The workspace is a disposable
    per-run directory holding only what was put there for this run, and reaching
    it from outside still requires traversing its parent, whose permissions are
    untouched. Nothing else is mounted.
    """
    if os.name != "posix":
        # Windows has no POSIX mode bits worth setting, and Docker Desktop
        # presents bind mounts as writable regardless.
        return

    for path in (workspace, *workspace.rglob("*")):
        try:
            mode = path.stat().st_mode
            # Directories need traverse as well as write; files need neither.
            path.chmod(mode | (0o007 if path.is_dir() else 0o006))
        except OSError:
            # A path that cannot be adjusted is left alone. If it turns out to
            # matter the command fails with a clear permission error, which is
            # a better outcome than refusing to run over a file nothing touches.
            logger.debug("workspace_chmod_skipped", path=str(path))


def run(
    argv: list[str],
    *,
    workspace: Path,
    image: str = DEFAULT_IMAGE,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    memory: str = DEFAULT_MEMORY,
    cpus: str = DEFAULT_CPUS,
) -> SandboxResult:
    """Run ``argv`` inside a container over ``workspace``.

    Returns a result for a command that failed; raises only when the sandbox
    itself could not be established. The distinction matters: "your tests
    failed" is an answer, "we could not isolate execution" is a refusal.
    """
    if not is_available():
        raise SandboxUnavailableError(
            "Docker is not available, so code cannot be executed safely. "
            "Start Docker and try again."
        )
    if not workspace.is_dir():
        raise SandboxError("The workspace directory does not exist")
    _grant_workspace_access(workspace)
    if not image_exists(image):
        raise SandboxUnavailableError(
            f"The sandbox image '{image}' is not built. Build it first: "
            "docker build -f docker/sandbox.Dockerfile -t aisep-sandbox:latest ."
        )

    container_name = f"aisep-sandbox-{uuid.uuid4().hex[:12]}"
    command = build_command(
        container_name=container_name,
        workspace=workspace,
        image=image,
        argv=argv,
        memory=memory,
        cpus=cpus,
    )

    started = time.perf_counter()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as expired:
        timed_out = True
        stdout = _decode(expired.stdout)
        stderr = _decode(expired.stderr)
        # subprocess.run only kills the docker *client*; the container keeps
        # running. It has to be killed explicitly or the timeout is a lie.
        _force_remove(container_name)
        exit_code = 124  # conventional timeout exit code
    except OSError as exc:
        raise SandboxUnavailableError("Docker could not be invoked") from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout, out_truncated = _truncate(stdout)
    stderr, err_truncated = _truncate(stderr)

    logger.info(
        "sandbox_run_complete",
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=duration_ms,
    )

    return SandboxResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        timed_out=timed_out,
        truncated=out_truncated or err_truncated,
        command=command,
    )


def _force_remove(container_name: str) -> None:
    """Kill a container and wait until it is actually gone.

    Issuing kill and rm is not enough. The container is started with ``--rm``,
    so the daemon begins its own auto-removal as soon as the process dies, and
    an explicit ``docker rm`` racing that auto-removal is rejected with
    "removal already in progress" -- a failure that means teardown is under way,
    not that it failed. Either path removes the container, but both are
    asynchronous: the commands return while the container is still listed.

    So this waits for the end state rather than assuming the commands produced
    it. The module promises guaranteed teardown, and returning while a container
    is still present makes that promise depend on timing. It held on Windows and
    broke on Linux, where the first CI run caught it.
    """
    for action in ("kill", "rm"):
        try:
            subprocess.run(
                ["docker", action, container_name],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logger.warning("sandbox_teardown_failed", container=container_name, action=action)

    deadline = time.monotonic() + TEARDOWN_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _container_exists(container_name):
            return
        time.sleep(0.1)

    # Reported rather than raised. The caller already has a result to return,
    # and a leaked container is an operational problem, not a reason to discard
    # the run's output.
    logger.warning("sandbox_container_still_present", container=container_name)


def _container_exists(container_name: str) -> bool:
    """Whether the daemon still lists the container, in any state."""
    try:
        listed = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # Cannot tell, so do not claim it is gone.
        return True
    return container_name in listed.stdout


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS] + "\n... (output truncated)", True


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw
