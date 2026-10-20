"""Disposable pinned-uv proof: cache policy and frozen empty-cache reconstruction."""
from dataclasses import replace
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import threading

from docassemble_executor.authority import MutationLock
from docassemble_executor.cache import CacheError, manage_cache
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.uvsync import sync_frozen

ROOT = Path('/tmp/cache-proof')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ROOT.mkdir(mode=0o755)
    account = pwd.getpwnam(os.environ['DA_ENVIRONMENT_USER'])
    state = ROOT / 'state'
    state.mkdir()
    os.environ['DA_MANAGED_STATE_DIR'] = str(state)
    paths = replace(ExecutorPaths.from_env(), state_dir=state)
    paths.ensure_directories()
    # The production preparation daemon is non-root. Verify it can take the
    # root-provisioned lock and that the operator cannot clear beneath it.
    ready = Path(account.pw_dir) / 'cache-proof-lock-ready'
    release = Path(account.pw_dir) / 'cache-proof-lock-release'
    script = ('from pathlib import Path; import time; '
              'from docassemble_executor.authority import MutationLock; '
              f'\nwith MutationLock(Path({str(state)!r})):\n '
              f'Path({str(ready)!r}).touch()\n '
              f'while not Path({str(release)!r}).exists(): time.sleep(.02)')
    daemon = subprocess.Popen(['runuser', '-u', account.pw_name, '--',
                               '/usr/share/docassemble/executor-venv/bin/python', '-c', script])
    import time
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists(), 'non-root preparation could not acquire mutation lock'
        try:
            manage_cache(paths, 'clear')
        except CacheError as error:
            assert 'active' in str(error)
        else:
            raise AssertionError('maintenance overlapped non-root preparation')
    finally:
        release.touch()
        daemon.wait(timeout=10)
    trace = ROOT / 'uv-commands.log'
    trace.touch()
    trace.chmod(0o666)
    wrapper = ROOT / 'uv-trace'
    wrapper.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> ' + str(trace) + '\nexec /usr/local/bin/uv "$@"\n')
    wrapper.chmod(0o755)
    paths = replace(paths, uv_binary=str(wrapper))
    measurements = [manage_cache(paths, 'usage')]
    upstream = ROOT / 'upstream'
    upstream.mkdir()
    wheel = next((paths.bundles_dir / 'core-r1/wheels').glob('titlecase-*.whl'))
    shutil.copy2(wheel, upstream / wheel.name)

    class Handler(SimpleHTTPRequestHandler):
        available = True
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(upstream), **kwargs)
        def log_message(self, *args):
            pass
        def do_GET(self):
            if not self.available:
                self.send_error(503, 'fixture upstream unavailable')
                return
            super().do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bundle = ROOT / 'retained-bundle'
    bundle.mkdir()
    (bundle / 'pyproject.toml').write_text(
        '[project]\nname="cache-proof"\nversion="1.0"\nrequires-python="==3.12.*"\n'
        f'dependencies=["titlecase @ http://127.0.0.1:{server.server_port}/{wheel.name}"]\n'
        '[tool.uv]\npackage=false\n')
    # Fixture setup is the ONLY solve. Operator/cache and all reconstruction
    # actions below consume this retained lock; they cannot solve or substitute.
    subprocess.run(['/usr/local/bin/uv', 'lock', '--project', str(bundle), '--no-config',
                    '--cache-dir', str(ROOT / 'fixture-cache')], check=True)
    retained = [bundle / 'pyproject.toml', bundle / 'uv.lock']
    for name in ('desired.json', 'lifecycle.json', 'approval.json', 'committed-auth', 'uploaded.zip', 'mandatory.whl'):
        file = state / name
        file.write_bytes(b'retained evidence, not cache')
        retained.append(file)
    before = {str(file): sha(file) for file in retained}
    measurements.append(manage_cache(paths, 'clear'))
    assert not any(paths.cache_dir.iterdir())
    env = {'UV_HTTP_RETRIES': '0', 'UV_HTTP_TIMEOUT': '3'}

    def materialize(project, name):
        venv = ROOT / name
        venv.mkdir()
        os.chown(venv, account.pw_uid, account.pw_gid)
        with MutationLock(state):
            result = sync_frozen(project, venv, paths.uv_binary, paths.cache_dir,
                                 log_dir=ROOT / 'logs', env_extra=env)
        return result

    try:
        installed = materialize(bundle, 'fresh-runtime')
        assert installed.ok, installed.describe()
        subprocess.run([str(ROOT / 'fresh-runtime/bin/python'), '-c',
                        'import titlecase; assert titlecase.titlecase("cache proof") == "Cache Proof"'], check=True)
        measurements.append(manage_cache(paths, 'usage'))
        measurements.append(manage_cache(paths, 'prune'))
        measurements.append(manage_cache(paths, 'clear'))
        assert not any(paths.cache_dir.iterdir())
        Handler.available = False
        unavailable = materialize(bundle, 'unavailable-runtime')
        assert not unavailable.ok
        assert 'Restore access and explicitly retry the same revision' in unavailable.describe()
        assert '503' in unavailable.output_tail
        assert before == {str(file): sha(file) for file in retained}
        Handler.available = True
        measurements.append(manage_cache(paths, 'clear'))
        recovered = materialize(bundle, 'recovered-runtime')
        assert recovered.ok, recovered.describe()
        # Exercise the real retained image core recovery bundle, not only the
        # small controlled unavailable-source fixture, from another empty cache.
        measurements.append(manage_cache(paths, 'clear'))
        core = materialize(paths.bundles_dir / 'core-r1', 'core-recovery')
        assert core.ok, core.describe()
        subprocess.run([str(ROOT / 'core-recovery/bin/python'), '-c',
                        'import docassemble.base, docassemble.webapp, docassemble.demo'], check=True)
        measurements.append(manage_cache(paths, 'usage'))
        assert before == {str(file): sha(file) for file in retained}
        commands = trace.read_text().splitlines()
        assert not any(command.startswith('lock ') for command in commands)
        assert all(command.startswith(('sync --frozen ', 'cache ', '--version')) for command in commands)
        # The environment identity still owns a cache writable by frozen sync.
        assert paths.cache_dir.stat().st_uid == account.pw_uid
        result = {'result': 'PASS', 'uv': '0.12.21', 'measurements': measurements,
                  'upstream_empty_cache_install': installed.ok,
                  'unavailable_source_failed': not unavailable.ok,
                  'unavailable_guidance': unavailable.describe(),
                  'exact_revision_retry_empty_cache': recovered.ok,
                  'real_image_core_empty_cache_recovery': core.ok,
                  'retained_evidence_unchanged': True, 'post_setup_solves': 0,
                  'non_root_preparation_excludes_maintenance': True,
                  'commands': commands}
        (ROOT / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
