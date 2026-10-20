"""Service commands refuse before importing a closed/recovery runtime."""
import os
import subprocess
import sys

import pytest
from docassemble_executor.admission import Admission


def test_role_busy_authority_is_specific_and_descriptor_owned(tmp_path):
    from docassemble_executor.capabilities import Capabilities, role_busy
    admission = Admission(tmp_path)
    admission.open(consumers=True)
    assert not role_busy(tmp_path, 'nltk')
    with Capabilities(tmp_path, role='conversion').accept() as caller:
        assert caller is not None
        assert role_busy(tmp_path, 'conversion')
        assert not role_busy(tmp_path, 'nltk')
        admission.prepare()
        assert role_busy(tmp_path, 'conversion')
    assert not role_busy(tmp_path, 'conversion')


def test_requalification_cannot_overwrite_an_intervening_close(tmp_path):
    admission = Admission(tmp_path)
    admission.prepare()
    admission.close('interrupted handoff')
    assert not admission.prepare(expected_mode='open')
    assert not admission.open(consumers=True, expected_mode='preparing')
    assert admission.state()['mode'] == 'closed'
    admission.prepare()
    assert admission.open(consumers=True, expected_mode='preparing')


def test_preparing_roles_does_not_admit_manual_application_work(tmp_path):
    admission = Admission(tmp_path)
    admission.prepare()
    result = subprocess.run(
        [sys.executable, '-m', 'docassemble_executor.cli', 'run-consumer', '--', '/usr/bin/true'],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path)),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 75, result.stdout + result.stderr
    with admission.request() as allowed:
        assert not allowed


def test_fixed_service_can_start_behind_closed_public_admission(tmp_path):
    for parent in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        parent.chmod(0o755)
    jobs = tmp_path / 'jobs'
    jobs.mkdir(mode=0o777)
    jobs.chmod(0o777)
    runtime = tmp_path / 'runtime/bin'
    runtime.mkdir(parents=True)
    marker = jobs / 'role-started'
    (runtime / 'python').write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    (runtime / 'python').chmod(0o755)
    (tmp_path / 'service-nltk.lock').touch()
    Admission(tmp_path).prepare()
    result = subprocess.run(
        [sys.executable, '-m', 'docassemble_executor.cli', 'run-service', '--kind', 'nltk'],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path),
                 DA_RUNTIME_VENV=str(runtime.parent), DA_LOG_DIR=str(jobs)),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0 and marker.exists(), result.stdout + result.stderr
    with Admission(tmp_path).request() as allowed:
        assert not allowed


@pytest.mark.parametrize('kind', ['websockets', 'nltk', 'conversion'])
def test_replacement_role_refuses_open_admission_before_runtime_entry(tmp_path, kind):
    for parent in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        parent.chmod(0o755)
    jobs = tmp_path / 'jobs'
    jobs.mkdir(mode=0o777)
    jobs.chmod(0o777)
    runtime = tmp_path / 'runtime/bin'
    runtime.mkdir(parents=True)
    (runtime / 'python').write_text(f'#!/bin/sh\ntouch "{jobs}/must-not-import"\n')
    (runtime / 'python').chmod(0o755)
    (tmp_path / ('service-' + kind + '.lock')).touch()
    Admission(tmp_path).open(consumers=True)
    result = subprocess.run(
        [sys.executable, '-m', 'docassemble_executor.cli', 'run-service', '--kind', kind],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path),
                 DA_RUNTIME_VENV=str(runtime.parent), DA_LOG_DIR=str(jobs)),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 75, result.stdout + result.stderr
    assert not (jobs / 'must-not-import').exists()
    assert Admission(tmp_path).state()['mode'] == 'open'


@pytest.mark.parametrize('kind', ['websockets', 'nltk', 'conversion'])
@pytest.mark.parametrize('recovery', [False, True])
def test_service_launch_stays_outside_closed_or_recovery_runtime(tmp_path, kind, recovery):
    admission = Admission(tmp_path)
    if recovery:
        admission.open()
    else:
        admission.close('maintenance')
    result = subprocess.run(
        [sys.executable, '-m', 'docassemble_executor.cli', 'run-service', '--kind', kind],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path),
                 DA_RUNTIME_VENV=str(tmp_path / 'missing-runtime')),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 75, result.stdout + result.stderr
