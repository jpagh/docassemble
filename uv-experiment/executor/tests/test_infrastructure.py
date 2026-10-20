"""Independent backup/shutdown exclusion at the CLI and admission boundary."""
import json
import os
import subprocess
import shlex
import sys
import time

import pytest

from docassemble_executor.admission import Admission


@pytest.mark.skipif(os.geteuid() != 0, reason='trusted backup environment requires the native root operator')
def test_shutdown_closure_refuses_new_backup_before_its_script_runs(tmp_path):
    root = tmp_path / 'root'
    (root / 'webapp').mkdir(parents=True)
    marker = tmp_path / 'backup-ran'
    (root / 'webapp/backup.sh').write_text(f'#!/bin/bash\ntouch "{marker}"\n')
    environment = tmp_path / 'backup.json'
    environment.write_text(json.dumps({}))
    environment.chmod(0o600)
    Admission(tmp_path / 'state/infrastructure').close('shutdown')
    result = subprocess.run([sys.executable, '-m', 'docassemble_executor.cli', 'run-backup', '--environment', str(environment)],
                            env=dict(os.environ, DA_ROOT=str(root), DA_MANAGED_STATE_DIR=str(tmp_path / 'state')),
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 75 and not marker.exists(), result.stdout + result.stderr


@pytest.mark.skipif(os.geteuid() != 0 or sys.platform != 'linux', reason='real subreaper and trusted root backup')
def test_accepted_backup_descendant_blocks_shutdown_after_exec_launcher_dies(tmp_path):
    root = tmp_path / 'root'
    (root / 'webapp').mkdir(parents=True)
    started, release = tmp_path / 'started', tmp_path / 'release'
    child = tmp_path / 'child.py'
    child.write_text(f'''import os, time
from pathlib import Path
os.setsid()
os.closerange(3, 1024)
Path({str(started)!r}).touch()
while not Path({str(release)!r}).exists(): time.sleep(0.02)
''')
    payload = shlex.join([sys.executable, '-c', f'import subprocess; subprocess.Popen([{sys.executable!r}, {str(child)!r}], close_fds=True)'])
    (root / 'webapp/backup.sh').write_text('#!/bin/bash\n' + payload + '\n')
    environment = tmp_path / 'backup.json'
    environment.write_text('{}')
    environment.chmod(0o600)
    gate = Admission(tmp_path / 'state/infrastructure')
    gate.open(consumers=True)
    launcher = subprocess.Popen([sys.executable, '-m', 'docassemble_executor.cli', 'run-backup', '--environment', str(environment)],
                                env=dict(os.environ, DA_ROOT=str(root), DA_MANAGED_STATE_DIR=str(tmp_path / 'state')),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not started.exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        launcher.kill()
        launcher.communicate(timeout=5)
        gate.close('shutdown')
        assert not gate.drain(0.05), 'backup descendant lost infrastructure exclusion'
        release.touch()
        assert gate.drain(5)
    finally:
        release.touch()
        if launcher.poll() is None:
            launcher.kill()
        launcher.communicate(timeout=5)
