"""File filtering.

The secret exclusions are a security control, not a tuning knob (docs/rag.md):
a file that slips through is embedded, sent to the LLM, and quotable in an
answer. They are tested first and hardest.
"""

from __future__ import annotations

import pytest

from app.ingestion.filters import (
    MAX_FILE_BYTES,
    SkipReason,
    classify,
    is_secret_path,
)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".env.production",
        "config/.env",
        "deploy/id_rsa",
        "keys/id_ed25519",
        "certs/server.pem",
        "certs/private.key",
        "app/credentials.json",
        "gcp/service-account.json",
        "secrets.yaml",
        "infra/terraform.tfvars",
        ".npmrc",
        ".netrc",
        "keystore.jks",
        "backup.kdbx",
    ],
)
def test_secret_files_are_never_indexed(path: str) -> None:
    assert is_secret_path(path) is True
    decision = classify(path, 100)
    assert decision.include is False
    assert decision.reason is SkipReason.secret


@pytest.mark.parametrize(
    "path", [".env.example", ".env.sample", ".env.template", ".env.defaults"]
)
def test_placeholder_env_files_are_indexed(path: str) -> None:
    """These document what configuration exists and hold no real values."""
    assert is_secret_path(path) is False
    assert classify(path, 100).include is True


def test_secret_check_runs_before_the_size_and_binary_checks() -> None:
    """Ordering matters: a secret must not be reported as merely 'too large'.

    If the cheap checks ran first, reordering them later could silently let a
    credential through while the tests still passed.
    """
    decision = classify("secrets/id_rsa", MAX_FILE_BYTES * 10)
    assert decision.reason is SkipReason.secret


def test_a_secret_inside_an_excluded_directory_is_still_reported_as_secret() -> None:
    assert classify("node_modules/pkg/.env", 50).reason is SkipReason.secret


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("node_modules/react/index.js", SkipReason.excluded_directory),
        ("frontend/node_modules/x/y.js", SkipReason.excluded_directory),
        ("backend/.venv/lib/thing.py", SkipReason.excluded_directory),
        ("app/__pycache__/mod.pyc", SkipReason.excluded_directory),
        ("dist/bundle.js", SkipReason.excluded_directory),
        ("package-lock.json", SkipReason.excluded_filename),
        ("Cargo.lock", SkipReason.excluded_filename),
        ("static/app.min.js", SkipReason.generated),
        ("proto/service_pb2.py", SkipReason.generated),
        ("assets/logo.png", SkipReason.binary),
        ("assets/app.wasm", SkipReason.binary),
    ],
)
def test_noise_is_excluded_with_the_right_reason(path: str, reason: SkipReason) -> None:
    decision = classify(path, 1000)
    assert decision.include is False
    assert decision.reason is reason


def test_directory_matching_is_per_segment_not_substring() -> None:
    """"dist" as a directory is noise; "dist" inside a filename is not."""
    assert classify("src/dist_helper.py", 100).include is True
    assert classify("src/my_build_tool.py", 100).include is True
    assert classify("src/dist/out.js", 100).include is False


def test_size_and_emptiness_bounds() -> None:
    assert classify("src/big.py", MAX_FILE_BYTES + 1).reason is SkipReason.too_large
    assert classify("src/ok.py", MAX_FILE_BYTES).include is True
    assert classify("src/empty.py", 0).reason is SkipReason.empty


def test_ordinary_source_is_indexed() -> None:
    for path in ("src/main.py", "README.md", "app/routes/auth.py", "lib/util.ts"):
        assert classify(path, 500).include is True, path


def test_windows_separators_are_handled() -> None:
    """Paths are normalised, so the same rules apply whatever produced them."""
    assert classify("node_modules\\react\\index.js", 100).include is False
    assert classify("config\\.env", 100).reason is SkipReason.secret
