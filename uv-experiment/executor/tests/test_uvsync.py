"""Exercise frozen sync through its external executable boundary."""

import tempfile
from pathlib import Path

import pytest

from docassemble_executor.authority import MutationLock
from docassemble_executor.uvsync import sync_frozen


@pytest.fixture(autouse=True)
def authority(tmp_path, monkeypatch):
    monkeypatch.setenv('DA_MANAGED_STATE_DIR', str(tmp_path))
    with MutationLock(tmp_path):
        yield


def test_recovery_forces_reinstallation_even_if_metadata_says_installed(tmp_path):
    # The executable boundary must remain traversable when root correctly
    # drops to the management account. pytest's root tmp tree is private.
    with tempfile.TemporaryDirectory() as directory:
        tools = Path(directory)
        tools.chmod(0o755)
        uv = tools / "uv"
        uv.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
        uv.chmod(0o755)
        logs = tmp_path / "logs"
        result = sync_frozen(tmp_path, tmp_path / "venv", str(uv), tmp_path / "cache", logs, reinstall=True)
    assert result.ok, result.describe()
    # Observe what the executable receives, not its privilege wrapper or the
    # controller's private staging path.
    arguments = result.output_tail.splitlines()
    assert arguments[:3] == ["sync", "--frozen", "--project"]
    assert arguments[4:] == ["--no-build", "--reinstall"]


def test_unavailable_tooling_reports_failure_instead_of_crashing(tmp_path):
    result = sync_frozen(tmp_path, tmp_path / 'venv', str(tmp_path / 'missing-uv'), tmp_path / 'cache')
    assert not result.ok
    assert 'missing-uv' in result.describe()
