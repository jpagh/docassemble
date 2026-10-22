"""OS authority survives controller interruption (no heartbeat takeover)."""
import os
import subprocess
import sys
import time

import pytest

from docassemble_executor.authority import MutationLock, guarded_popen


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux subreaper contract; verified in native container')
def test_detached_grandchild_retains_authority_even_after_parent_exits(tmp_path):
    ready = tmp_path / 'detached'
    with MutationLock(tmp_path) as lock:
        guardian = guarded_popen([sys.executable, '-c', '''
import os, sys, time
from pathlib import Path
if os.fork() == 0:
    os.setsid()
    os.closerange(3, 1024)
    Path(sys.argv[1]).touch()
    time.sleep(2)
    os._exit(0)
''', str(ready)], lock.fd)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        with pytest.raises(BlockingIOError), MutationLock(tmp_path):
            pass
        assert guardian.poll() is None
        guardian.wait(timeout=5)
        with MutationLock(tmp_path):
            pass
    finally:
        guardian.wait(timeout=5)


@pytest.mark.skipif(os.geteuid() != 0, reason='root refusal verified in native container')
def test_root_cannot_select_itself_as_third_party_builder(tmp_path, monkeypatch):
    from docassemble_executor.uvsync import sync_frozen

    from docassemble.base.managed_python import python_requires
    import json
    (tmp_path / 'pyproject.toml').write_text('[project]\nrequires-python=' + json.dumps(python_requires()))
    (tmp_path / 'uv.lock').write_text('requires-python=' + json.dumps(python_requires()))
    monkeypatch.setenv('DA_ENVIRONMENT_USER', 'root')
    with pytest.raises(ValueError, match='never run as root'):
        sync_frozen(tmp_path, tmp_path / 'venv', 'uv', tmp_path / 'cache')


def test_live_guarded_child_excludes_mutation_after_controller_dies(tmp_path):
    ready = tmp_path / 'ready'
    controller = subprocess.Popen([sys.executable, '-c', '''
import sys, time
from pathlib import Path
from docassemble_executor.authority import MutationLock, guarded_popen
with MutationLock(Path(sys.argv[1])) as lock:
    guarded_popen([sys.executable, '-c', 'import time; time.sleep(2)'], lock.fd)
    Path(sys.argv[2]).touch()
    time.sleep(10)
''', str(tmp_path), str(ready)])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        controller.kill()
        controller.wait()
        with pytest.raises(BlockingIOError), MutationLock(tmp_path):
            pass
        deadline = time.monotonic() + 5
        while True:
            try:
                with MutationLock(tmp_path):
                    break
            except BlockingIOError:
                assert time.monotonic() < deadline
                time.sleep(0.02)
    finally:
        if controller.poll() is None:
            controller.kill()
        controller.wait()
