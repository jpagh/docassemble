"""Managed helper entrypoints and accepted nested process lifetimes."""
import os
import subprocess
import sys
import time

import pytest

from docassemble_executor.admission import Admission


@pytest.mark.parametrize('kind', ['cron', 'hourly', 'daily', 'weekly', 'monthly', 'mail'])
@pytest.mark.parametrize('recovery', [False, True])
def test_helper_refuses_before_entering_closed_or_recovery_runtime(tmp_path, kind, recovery):
    admission = Admission(tmp_path)
    if recovery:
        admission.open()
    else:
        admission.close('maintenance')
    result = subprocess.run(
        [sys.executable, '-m', 'docassemble_executor.cli', 'run-helper', '--kind', kind],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path),
                 DA_RUNTIME_VENV=str(tmp_path / 'missing-runtime')),
        input='unaccepted mail', capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 75, result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['hourly', 'daily', 'weekly', 'monthly'])
def test_closed_runtime_schedule_is_skipped_without_waiting(tmp_path, kind):
    Admission(tmp_path).close('maintenance')
    result = subprocess.run(
        [sys.executable, '-m', 'docassemble_executor.cli', 'run-schedule', '--kind', kind],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path),
                 DA_RUNTIME_VENV=str(tmp_path / 'missing-runtime')),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 75, result.stdout + result.stderr


def test_accepted_job_can_launch_nested_helper_after_closure(tmp_path):
    # Root guardians drop payloads to www-data in native verification.
    for directory in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        directory.chmod(0o755)
    jobs = tmp_path / 'jobs'
    jobs.mkdir(mode=0o777)
    jobs.chmod(0o777)
    started, release = jobs / 'started', jobs / 'release'
    admission = Admission(tmp_path)
    admission.open(consumers=True)
    command = [sys.executable, '-m', 'docassemble_executor.cli', 'run-consumer', '--']
    job = subprocess.Popen(command + [sys.executable, '-c', '''
import subprocess, sys, time
from pathlib import Path
started, release = map(Path, sys.argv[1:3])
started.touch()
while not release.exists():
    time.sleep(0.02)
sys.exit(subprocess.call(sys.argv[3:]))
''', str(started), str(release), *command, '/usr/bin/printf', 'nested job finished'],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path)),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not started.exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        admission.close('draining', mode='draining')
        assert not admission.drain(0.05)
        release.touch()
        stdout, stderr = job.communicate(timeout=10)
        assert job.returncode == 0, stdout + stderr
        assert stdout == 'nested job finished'
        assert admission.drain(5)
    finally:
        release.touch()
        job.communicate(timeout=10)
