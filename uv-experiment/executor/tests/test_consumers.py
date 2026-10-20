"""Public admitted-consumer command and lifetime authority."""
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile

import pytest

from docassemble_executor.admission import Admission
from docassemble_executor.authority import RuntimeLock


def launch(state, command, *options):
    return subprocess.Popen(
        [sys.executable, '-m', 'docassemble_executor.cli', 'run-consumer',
         *options, '--', *command],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(state)),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        assert time.monotonic() < deadline, 'consumer did not start'
        time.sleep(0.02)


@pytest.fixture(autouse=True)
def job_files(tmp_path):
    # Allow payload traversal, but keep admission/runtime authority root-owned.
    for path in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        path.chmod(0o755)
    jobs = tmp_path / 'jobs'
    jobs.mkdir(mode=0o777)
    jobs.chmod(0o777)
    return jobs


@pytest.mark.skipif(os.geteuid() != 0, reason='payload privilege drop verified in native container')
def test_root_launcher_never_runs_payload_as_root(tmp_path, monkeypatch):
    # A runtime-installed executable must not shadow the privileged OS launcher.
    tools = tmp_path / 'jobs'
    (tools / 'runuser').write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
    (tools / 'runuser').chmod(0o755)
    monkeypatch.setenv('PATH', str(tools) + ':' + os.environ['PATH'])
    Admission(tmp_path).open(consumers=True)
    child = launch(tmp_path, ['/usr/bin/id', '-u'])
    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 0, stderr
    assert stdout.strip() == '33'  # the fixed www-data account, not controller root


def test_managed_finite_job_preserves_caller_pythonpath(tmp_path, monkeypatch):
    modules = tmp_path / 'jobs'
    (modules / 'caller_module.py').write_text("VALUE = 'caller module loaded'\n")
    # Keep the independent CLI importable as well as the caller's own module.
    source = str(Path(__file__).resolve().parents[1] / 'src')
    monkeypatch.setenv('PYTHONPATH', source + ':' + str(modules))
    Admission(tmp_path).open(consumers=True)
    child = launch(tmp_path, [sys.executable, '-c', 'import caller_module; print(caller_module.VALUE)'])
    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 0, stderr
    assert stdout.strip() == 'caller module loaded'


def test_closed_consumer_gate_rejects_before_running_command(tmp_path):
    Admission(tmp_path).close('maintenance')
    marker = tmp_path / 'executed'
    child = launch(tmp_path, [sys.executable, '-c', f'open({str(marker)!r}, "w").close()'])
    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 75, stdout + stderr
    assert not marker.exists()


def test_supervised_launcher_waits_without_running_payload_until_normal_admission(tmp_path):
    admission = Admission(tmp_path)
    admission.close('maintenance')
    marker = tmp_path / 'jobs/executed'
    child = launch(tmp_path, [sys.executable, '-c', f'open({str(marker)!r}, "w").close()'], '--wait')
    try:
        time.sleep(0.2)
        assert child.poll() is None
        assert not marker.exists()
        admission.open(consumers=True)
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, stdout + stderr
        assert marker.exists()
    finally:
        if child.poll() is None:
            child.terminate()
        child.communicate(timeout=5)


def test_accepted_job_blocks_drain_until_completion_even_after_launcher_dies(tmp_path):
    admission = Admission(tmp_path)
    admission.open(consumers=True)
    started, release, completed = (tmp_path / 'jobs' / name for name in ('started', 'release', 'completed'))
    child = launch(tmp_path, [sys.executable, '-c', '''
import sys, time
from pathlib import Path
started, release, completed = map(Path, sys.argv[1:])
started.touch()
while not release.exists():
    time.sleep(0.02)
completed.write_text('accepted work finished')
''', str(started), str(release), str(completed)])
    try:
        wait_for(started)
        admission.close('maintenance', mode='draining')
        assert not admission.drain(0.05)
        with pytest.raises(BlockingIOError), RuntimeLock(tmp_path):
            pass
        child.kill()
        child.wait(timeout=5)
        assert not admission.drain(0.05)
        release.touch()
        wait_for(completed)
        assert admission.drain(5)
        with RuntimeLock(tmp_path):
            pass
    finally:
        release.touch()
        child.communicate(timeout=5)


@pytest.mark.parametrize('script', [
    'run-celery.sh', 'run-celery-single.sh', 'run-websockets.sh', 'run-nltk.sh',
    'run-cron.sh', 'process-email.sh', 'run-unoserver.sh',
    'cron/docassemble-cron-hourly.sh', 'cron/docassemble-cron-daily.sh',
    'cron/docassemble-cron-weekly.sh', 'cron/docassemble-cron-monthly.sh',
])
def test_shell_consumers_refuse_closed_admission_before_runtime_activation(tmp_path, script):
    repo = Path(os.environ.get('DA_TEST_REPO', str(Path(__file__).resolve().parents[3])))
    runtime = tmp_path / 'runtime/bin'
    runtime.mkdir(parents=True)
    marker = tmp_path / 'activated'
    (runtime / 'activate').write_text(f'touch "{marker}"; exit 94\n')
    admission = Admission(tmp_path / 'state')
    admission.close('maintenance')
    result = subprocess.run(['bash', str(repo / 'Docker' / script)], capture_output=True, text=True,
                            env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(admission.state_dir),
                                     DA_PYTHON=str(runtime.parent)), timeout=5)
    assert result.returncode == 75, result.stdout + result.stderr
    assert not marker.exists()


@pytest.mark.parametrize('script', [
    'run-celery.sh', 'run-websockets.sh', 'run-nltk.sh', 'run-cron.sh', 'process-email.sh',
    'cron/docassemble-cron-hourly.sh', 'cron/docassemble-cron-daily.sh',
    'cron/docassemble-cron-weekly.sh', 'cron/docassemble-cron-monthly.sh',
])
@pytest.mark.parametrize('adopted', [False, True])
def test_flag_off_preserves_legacy_only_without_durable_managed_evidence(tmp_path, adopted, script):
    repo = Path(os.environ.get('DA_TEST_REPO', str(Path(__file__).resolve().parents[3])))
    state = tmp_path / 'state'
    state.mkdir()
    if adopted:
        (state / 'desired.json').touch()
    runtime = tmp_path / 'runtime/bin'
    runtime.mkdir(parents=True)
    marker = tmp_path / 'activated'
    (runtime / 'activate').write_text(f'touch "{marker}"; exit 94\n')
    result = subprocess.run(['bash', str(repo / 'Docker' / script)], capture_output=True, text=True,
                            env=dict(os.environ, DA_UV_MANAGED='false', DA_MANAGED_STATE_DIR=str(state),
                                     DA_PYTHON=str(runtime.parent)), timeout=5)
    assert result.returncode == (75 if adopted else 94), result.stderr
    assert marker.exists() is not adopted


@pytest.mark.skipif(sys.platform != 'linux', reason='detached descendants verified in native container')
def test_detached_job_descendant_retains_admission_and_runtime_authority(tmp_path):
    admission = Admission(tmp_path)
    admission.open(consumers=True)
    ready, release, completed = (tmp_path / 'jobs' / name for name in ('ready', 'release', 'completed'))
    child = launch(tmp_path, [sys.executable, '-c', '''
import os, sys, time
from pathlib import Path
if os.fork() == 0:
    os.setsid()
    os.closerange(3, 1024)
    ready, release, completed = map(Path, sys.argv[1:])
    ready.touch()
    while not release.exists():
        time.sleep(0.02)
    completed.write_text('detached work finished')
    os._exit(0)
''', str(ready), str(release), str(completed)])
    try:
        wait_for(ready)
        child.kill()
        child.wait(timeout=5)
        admission.close('draining', mode='draining')
        assert not admission.drain(0.05)
        with pytest.raises(BlockingIOError), RuntimeLock(tmp_path):
            pass
        release.touch()
        wait_for(completed)
        assert admission.drain(5)
        with RuntimeLock(tmp_path):
            pass
    finally:
        release.touch()
        child.communicate(timeout=5)


@pytest.mark.skipif(os.geteuid() != 0, reason='non-root reader verified in native container')
def test_unprivileged_consumer_can_read_but_not_rewrite_authority():
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory)
        state.chmod(0o755)
        admission = Admission(state)
        admission.open(consumers=True)
        command = ['runuser', '-u', 'www-data', '--', sys.executable, '-m',
                   'docassemble_executor.cli', 'run-consumer', '--', '/usr/bin/true']
        result = subprocess.run(command, capture_output=True, text=True,
                                env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=directory), timeout=5)
        assert result.returncode == 0, result.stdout + result.stderr
        assert admission.state()['consumers'] is True
        result = subprocess.run(['runuser', '-u', 'www-data', '--', '/usr/bin/touch',
                                 str(admission.path)], capture_output=True, text=True)
        assert result.returncode != 0


@pytest.mark.skipif(not Path('/usr/bin/supervisord').exists(), reason='real Supervisor verified in native container')
def test_supervisor_autorestart_stays_outside_runtime_until_preparing(tmp_path, monkeypatch):
    from docassemble_executor.paths import ExecutorPaths
    from docassemble_executor.supervision import start_supervisor, supervisor_command

    # Supervisor drops to www-data; grant traversal only, not state writes.
    for path in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        path.chmod(0o755)
    runtime = tmp_path / 'runtime/bin'
    runtime.mkdir(parents=True)
    marker = tmp_path / 'runtime-entered'
    marker.parent.chmod(0o777)  # test payload only; authority is in its own root directory
    (runtime / 'python').write_text(f'#!/bin/bash\ncase "$*" in *worker_runtime.py*|*service_runtime.py*) exit 0 ;; esac\n'
                                    f'touch "{marker}"\nexec /usr/bin/sleep 60\n')
    (runtime / 'python').chmod(0o755)
    for name, value in {'DA_MANAGED_STATE_DIR': tmp_path / 'state', 'DA_RUN_DIR': tmp_path / 'run',
                        'DA_LOG_DIR': tmp_path / 'log', 'DA_RUNTIME_VENV': runtime.parent}.items():
        monkeypatch.setenv(name, str(value))
    paths = ExecutorPaths.from_env()
    paths.ensure_directories()
    admission = Admission(paths.state_dir)
    admission.close('maintenance')
    try:
        check = start_supervisor(paths)
        assert check.ok, check.detail
        # This fixture's seam is watchdog autorestart. The real service/worker
        # protocol proofs exercise the other registered wrappers separately.
        assert supervisor_command(paths, ['stop', 'celery', 'celerysingle', 'websockets', 'nltk', 'conversion']).returncode == 0
        time.sleep(0.3)
        assert not marker.exists()
        admission.open(consumers=True)
        time.sleep(0.3)
        assert not marker.exists()  # Replacement imports need qualification first.
        admission.prepare()
        wait_for(marker)
        admission.open(consumers=True)
        admission.close('draining', mode='draining')
        assert admission.drain(5)
        with RuntimeLock(paths.state_dir):
            pass
        marker.unlink()
        assert supervisor_command(paths, ['restart', 'watchdog']).returncode == 0
        time.sleep(0.3)
        assert not marker.exists()
        old_pid = int(supervisor_command(paths, ['pid', 'watchdog']).stdout.strip())
        os.kill(old_pid, 9)  # exercise actual autorestart, not only supervisorctl restart
        deadline = time.monotonic() + 5
        while True:
            new_pid = int(supervisor_command(paths, ['pid', 'watchdog']).stdout.strip())
            if new_pid > 0 and new_pid != old_pid:
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert not marker.exists()
        admission.open(consumers=True)
        time.sleep(0.3)
        assert not marker.exists()
        admission.prepare()
        wait_for(marker)
        admission.open(consumers=True)
    finally:
        admission.close('test cleanup')
        assert admission.drain(5)
        supervisor_command(paths, ['shutdown'])


@pytest.mark.parametrize('kind', ['normal', 'single'])
def test_worker_launch_rejects_closed_admission_before_runtime_import(tmp_path, kind):
    admission = Admission(tmp_path)
    admission.close('maintenance')
    result = subprocess.run(
        [sys.executable, '-m', 'docassemble_executor.cli', 'run-worker', '--kind', kind],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path),
                 DA_RUNTIME_VENV=str(tmp_path / 'missing-runtime')),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 75, result.stdout + result.stderr


def test_recovery_http_admission_does_not_authorize_runtime_jobs(tmp_path):
    admission = Admission(tmp_path)
    admission.open()  # restricted recovery may answer HTTP, but not launch jobs
    marker = tmp_path / 'executed'
    child = launch(tmp_path, [sys.executable, '-c', f'open({str(marker)!r}, "w").close()'])
    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 75, stdout + stderr
    assert not marker.exists()
