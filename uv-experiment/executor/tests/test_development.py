"""Intentional development installation and ordinary reload at the CLI seam."""
import os
import subprocess
import sys

from docassemble_executor.admission import Admission
from docassemble_executor.authority import MutationLock

import pytest


def command(state, *args, **environment):
    return subprocess.run(
        [sys.executable, '-m', 'docassemble_executor.cli', *args],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(state),
                 **environment), capture_output=True, text=True, timeout=10,
    )


def test_development_install_refuses_closed_admission_before_source_execution(tmp_path):
    Admission(tmp_path).close('maintenance')
    source = tmp_path / 'package'
    source.mkdir()
    marker = tmp_path / 'built'
    (source / 'setup.py').write_text(f'open({str(marker)!r}, "w").close()\n')
    result = command(tmp_path, 'development-install', '--source', str(source))
    assert result.returncode == 75, result.stdout + result.stderr
    assert not marker.exists()


@pytest.mark.parametrize('operation', [['development-install', '--source', '/missing-source'], ['reload-web']])
def test_development_commands_refuse_recovery_without_importing_runtime(tmp_path, operation):
    Admission(tmp_path).open()  # HTTP recovery permission is not mutation permission.
    result = command(tmp_path, *operation)
    assert result.returncode == 75, result.stdout + result.stderr


@pytest.mark.parametrize('operation', [['development-install', '--source', '/missing-source'], ['reload-web']])
def test_development_commands_cannot_overlap_host_mutation_authority(tmp_path, operation):
    Admission(tmp_path).open(consumers=True)
    with MutationLock(tmp_path):
        result = command(tmp_path, *operation)
    assert result.returncode != 0 and 'temporarily unavailable' in result.stderr.lower()


@pytest.mark.skipif(not (os.geteuid() == 0 and sys.platform == 'linux'), reason='native/root shell transport proof')
@pytest.mark.parametrize('transport', ['docker', 'container'])
def test_missing_gate_and_invalid_parent_cannot_activate_legacy_runtime(tmp_path, transport):
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    source = tmp_path / 'package'
    source.mkdir()
    invalid = tmp_path / 'invalid'
    invalid.write_text('not a directory')
    activated = tmp_path / 'activated'
    activate = tmp_path / 'activate'
    activate.write_text(f'touch {str(activated)!r}\nexit 0\n')
    # Only exec/cp/inspect transport is adapted to a real local shell. The public
    # script, fallback filesystem checks and activation boundary are unchanged.
    command_adapter = tmp_path / 'transport'
    command_adapter.write_text('''#!/bin/bash
case "$1" in
inspect) if [ "$2" = -f ]; then echo true; else echo '{"state":"running"}'; fi ;;
cp) exit 0 ;;
exec) shift; while [ "$1" = -e ] || [ "$1" = --user ]; do
    if [ "$1" = -e ]; then export "$2"; fi; shift 2
    done; shift; exec "$@" ;;
esac
''')
    command_adapter.chmod(0o755)
    # On the root-only disposable image, hiding the stock gate makes this the
    # missing-helper branch. Preserve it for the next test/fixture.
    gate = Path('/usr/share/docassemble/webapp/managed-consumer.sh')
    saved = gate.with_suffix('.proof-disabled')
    gate.rename(saved)
    try:
        result = subprocess.run(['bash', str(root / 'development' / ('install-live-package-' + transport + '.sh')),
                                 str(source), 'fixture'], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, DA_UV_MANAGED='false', DA_MANAGED_STATE_DIR=str(invalid / 'child'),
                                         DA_ACTIVATE=str(activate), DOCKER=str(command_adapter), CONTAINER=str(command_adapter)))
        assert result.returncode == 75, result.stdout + result.stderr
        assert not activated.exists()
    finally:
        saved.rename(gate)


def test_disabling_managed_flag_does_not_authorize_development_install(tmp_path):
    Admission(tmp_path).open(consumers=True)
    result = subprocess.run([sys.executable, '-m', 'docassemble_executor.cli', 'development-install', '--source', '/missing-source'],
                            env=dict(os.environ, DA_UV_MANAGED='false', DA_MANAGED_STATE_DIR=str(tmp_path)),
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and 'legacy boot refused' in result.stderr
