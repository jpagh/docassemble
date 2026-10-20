"""Proof orchestration safety using fake Docker only; never run a container."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

EXPERIMENT = Path(__file__).resolve().parents[2]


def load(name, monkeypatch):
    monkeypatch.syspath_prepend(str(EXPERIMENT))
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), EXPERIMENT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDocker:
    def __init__(self, *, containers=(), volumes=(), fail_export=False, fail_remove=False, linger=False):
        self.containers = set(containers)
        self.volumes = set(volumes)
        self.volume_contents = {name: b'sentinel bytes' for name in volumes}
        self.fail_export, self.fail_remove, self.linger = fail_export, fail_remove, linger
        self.commands = []

    def __call__(self, argv, **kwargs):
        self.commands.append(argv)
        code, output = 0, ''
        if argv[:3] == ['docker', 'ps', '-a']:
            output = '\n'.join(sorted(self.containers))
        elif argv[:3] == ['docker', 'volume', 'ls']:
            output = '\n'.join(sorted(self.volumes))
        elif argv[:3] == ['docker', 'volume', 'create']:
            self.volumes.add(argv[3])
        elif argv[:3] == ['docker', 'volume', 'rm']:
            code = int(self.fail_remove)
            if not code and not self.linger:
                self.volumes.discard(argv[3])
                self.volume_contents.pop(argv[3], None)
        elif argv[:2] == ['docker', 'rm']:
            code = int(self.fail_remove)
            if not code and not self.linger:
                self.containers.discard(argv[-1])
        elif argv[:2] == ['docker', 'run']:
            if '--name' in argv:
                self.containers.add(argv[argv.index('--name') + 1])
        elif argv[:2] == ['docker', 'cp']:
            code = int(self.fail_export)
        elif argv[:2] == ['docker', 'inspect']:
            output = 'false'
        return subprocess.CompletedProcess(argv, code, output, 'injected failure' if code else '')


DRIVER_RESOURCES = [
    ('checkpoint-restore', 'da-24-local-old', 'da-24-local-backup'),
    ('cold-restore', 'da-18-old', 'da-18-backup'),
    ('image-migration', 'da-19-source', 'da-19-files'),
    ('core-upgrade', 'da-14b-core-upgrade', None),
    ('historical-rollback', 'da-15-historical-rollback', None),
    ('managed-backup', 'da-17-managed-backup', None),
    ('cloud-checkpoint', 'da-22-cloud-checkpoint', None),
    ('legacy-adoption', 'da-16-legacy-adoption', None),
    ('checkpoint', 'da-21-checkpoint', None),
    ('image-bridge', 'da-05c2-image-bridge', None),
    ('installation', 'da-05c-installation-proof', None),
    ('cache', 'da-20-cache-proof', None),
]


@pytest.mark.parametrize('driver,container,volume,collision', [
    (driver, container, volume, collision)
    for driver, container, volume in DRIVER_RESOURCES
    for collision in (('container', 'volume') if volume else ('container',))
])
def test_destructive_drivers_refuse_collisions_before_any_mutation(
        monkeypatch, tmp_path, driver, container, volume, collision):
    module = load('run-' + driver + '-proof', monkeypatch)
    monkeypatch.setenv('PROOF_EVIDENCE_DIR', str(tmp_path))
    monkeypatch.setenv('PROOF_CHECKPOINT_PROVIDER', 'local')
    monkeypatch.delenv('PROOF_CONTAINER_NAME', raising=False)
    docker = FakeDocker(containers=[container] if collision == 'container' else [],
                        volumes=[volume] if collision == 'volume' else [])
    monkeypatch.setattr(module.subprocess, 'run', docker)
    with pytest.raises(RuntimeError, match='pre-existing proof resources'):
        module.main()
    assert not any(command[:2] in (['docker', 'rm'], ['docker', 'run']) or
                   command[:3] in (['docker', 'volume', 'rm'], ['docker', 'volume', 'create'])
                   for command in docker.commands)
    if collision == 'volume':
        assert volume in docker.volumes
        assert docker.volume_contents[volume] == b'sentinel bytes'
    else:
        assert container in docker.containers


def core_bootstrap_launchers(directory):
    """Discover run-*-proof.py callers of the two shared core proof scripts."""
    return sorted(path.stem for path in directory.glob('run-*-proof.py')
                  if any(script in path.read_text() for script in
                         ('core_upgrade_image_proof', 'historical_rollback_image_proof')))


# Discover at collection time so a new direct caller joins the ingress regression.
CORE_BOOTSTRAP_LAUNCHERS = core_bootstrap_launchers(EXPERIMENT)
INGRESS_VARIABLES = ('DA_MANAGED_ARTIFACT_ROOT', 'DA_MANAGED_ARTIFACT_UID',
                     'DA_EXECUTOR_ARTIFACT_ROOT', 'DA_MANAGED_SECRET_ROOT')


@pytest.mark.parametrize('script', ['core_upgrade_image_proof', 'historical_rollback_image_proof'])
def test_core_bootstrap_launcher_regression_discovers_new_callers(tmp_path, script):
    assert CORE_BOOTSTRAP_LAUNCHERS
    assert core_bootstrap_launchers(tmp_path) == []
    (tmp_path / 'run-new-proof.py').write_text('script = ' + repr(script + '.py'))
    (tmp_path / 'run-unrelated-proof.py').write_text('script = "other.py"')
    assert core_bootstrap_launchers(tmp_path) == ['run-new-proof']


@pytest.mark.parametrize('launcher', CORE_BOOTSTRAP_LAUNCHERS)
def test_core_bootstrap_launchers_preserve_complete_managed_ingress(monkeypatch, tmp_path, launcher):
    module = load(launcher, monkeypatch)
    monkeypatch.setenv('PROOF_EVIDENCE_DIR', str(tmp_path))
    monkeypatch.setenv('PROOF_CHECKPOINT_PROVIDER', 'local')
    for name in INGRESS_VARIABLES:
        monkeypatch.setenv(name, 'proof-inherited-' + name)
    docker = FakeDocker()
    bootstraps = []
    def execute(argv, **kwargs):
        if argv[-1] == 'bootstrap':
            bootstraps.append(argv)
            environment = kwargs.get('env', module.os.environ)
            assert all(environment[name] == 'proof-inherited-' + name for name in INGRESS_VARIABLES)
            assert not any(token in argv for token in ('-u', '--unset', '-i', '--ignore-environment'))
            assert not any(token.startswith('--unset=') or
                           token.split('=', 1)[0] in INGRESS_VARIABLES for token in argv)
            raise RuntimeError('stop after checking shared core bootstrap')
        result = docker(argv, **kwargs)
        if argv[:2] == ['docker', 'exec'] and 'sha256sum' in argv:
            path = argv[-1].replace('/opt/da-sources/', '')
            path = path.replace('/opt/docassemble-executor/', 'uv-experiment/executor/')
            result.stdout = module.hashlib.sha256((EXPERIMENT.parent / path).read_bytes()).hexdigest()
        return result
    monkeypatch.setattr(module.subprocess, 'run', execute)
    with pytest.raises(RuntimeError, match='stop after checking shared core bootstrap'):
        module.main()
    assert len(bootstraps) == 1
    if launcher == 'run-image-migration-proof':
        assert 'PYTHONDONTWRITEBYTECODE=1' in bootstraps[0]
    assert not docker.containers and not docker.volumes


@pytest.mark.parametrize('image', ['stale-old', 'stale-new'])
def test_image_migration_rejects_stale_schema_before_shared_resource_creation(monkeypatch, tmp_path, image):
    module = load('run-image-migration-proof', monkeypatch)
    monkeypatch.setenv('PROOF_EVIDENCE_DIR', str(tmp_path))
    monkeypatch.setenv('PROOF_OLD_IMAGE', 'stale-old')
    monkeypatch.setenv('PROOF_NEW_IMAGE', 'stale-new')
    docker = FakeDocker()
    def execute(argv, **kwargs):
        result = docker(argv, **kwargs)
        if argv[:2] == ['docker', 'run'] and argv[-2] == 'image':
            assert '--rm' in argv and argv[argv.index('--network') + 1] == 'none'
            expected = json.loads(argv[-1])
            assert 'docassemble/base/managed_preparation.py' in expected
            assert any('05b500000001' in member for member in expected)
            if image in argv:
                result.returncode, result.stderr = 1, 'missing Alembic migration 05b500000001'
        return result
    monkeypatch.setattr(module.subprocess, 'run', execute)
    with pytest.raises(RuntimeError, match='schema preflight failed for ' + image) as error:
        module.main()
    assert 'Rebuild the base fixture' in str(error.value)
    assert 'BOTH replacement images' in str(error.value)
    assert 'PROOF_OLD_IMAGE/PROOF_NEW_IMAGE' in str(error.value)
    label = 'old' if image == 'stale-old' else 'new'
    assert '05b500000001' in (tmp_path / (label + '-image-schema.json')).read_text()
    assert not any(command[:3] == ['docker', 'volume', 'create'] or '--name' in command
                   for command in docker.commands)


def test_image_migration_checks_live_schema_before_shared_bootstrap_and_cleans_failure(monkeypatch, tmp_path):
    module = load('run-image-migration-proof', monkeypatch)
    monkeypatch.setenv('PROOF_EVIDENCE_DIR', str(tmp_path))
    docker = FakeDocker()
    def execute(argv, **kwargs):
        result = docker(argv, **kwargs)
        if argv[-2:] == ['/repo/uv-experiment/preparation/image_migration_schema_probe.py', 'database']:
            result.returncode, result.stderr = 1, 'managed_candidate.credential_request_binding is missing'
        return result
    monkeypatch.setattr(module.subprocess, 'run', execute)
    with pytest.raises(RuntimeError, match='schema preflight failed'):
        module.main()
    assert not any(command[-1] == 'bootstrap' for command in docker.commands)
    assert 'credential_request_binding' in (tmp_path / 'da-19-source-schema.json').read_text()
    assert not docker.containers and not docker.volumes


def test_image_migration_verifies_both_images_and_each_deployment_schema(monkeypatch, tmp_path):
    module = load('run-image-migration-proof', monkeypatch)
    monkeypatch.setenv('PROOF_EVIDENCE_DIR', str(tmp_path))
    docker = FakeDocker()
    monkeypatch.setattr(module.subprocess, 'run', docker)
    module.main()
    image_checks = [command for command in docker.commands
                    if command[:2] == ['docker', 'run'] and command[-2] == 'image']
    assert len(image_checks) == 2
    creation = docker.commands.index(['docker', 'volume', 'create', 'da-19-database'])
    assert all(docker.commands.index(command) < creation for command in image_checks)
    database_checks = [command for command in docker.commands
                       if command[-2:] == ['/repo/uv-experiment/preparation/image_migration_schema_probe.py', 'database']]
    assert [command[2] for command in database_checks] == ['da-19-source', 'da-19-newer', 'da-19-older']
    for command in database_checks:
        name = command[2]
        next_step = next(step for step in docker.commands if step[:3] == ['docker', 'exec', name]
                         and any(token.endswith(('/image_bridge_proof.py', '/image_migration_proof.py')) for token in step))
        assert docker.commands.index(command) < docker.commands.index(next_step)
    assert not docker.containers and not docker.volumes


@pytest.mark.parametrize('location,defect', [
    ('runtime', 'missing'), ('runtime', 'changed'),
    ('core-r1', 'missing'), ('core-r1', 'changed'),
    ('runtime-r1', 'missing'), ('runtime-r1', 'changed'),
    (None, None),
])
def test_image_migration_schema_probe_verifies_installed_and_retained_schema_bytes(
        monkeypatch, tmp_path, location, defect):
    probe = load('preparation/image_migration_schema_probe', monkeypatch)
    migration = 'docassemble/webapp/alembic/versions/05b500000001_git_credential_request_binding.py'
    files = {'docassemble/base/managed_preparation.py': b'current schema', migration: b'current migration'}
    expected = {member: hashlib.sha256(data).hexdigest() for member, data in files.items()}
    runtime, bundles = tmp_path / 'runtime', tmp_path / 'bundles'
    for member, data in files.items():
        path = runtime / 'lib/python3.12/site-packages' / member
        path.parent.mkdir(parents=True, exist_ok=True)
        if location == 'runtime' and member == migration:
            if defect == 'missing':
                continue
            data = b'stale migration'
        path.write_bytes(data)
    for bundle in ('core-r1', 'runtime-r1'):
        wheels = bundles / bundle / 'wheels'
        wheels.mkdir(parents=True)
        for project in ('base', 'webapp'):
            with zipfile.ZipFile(wheels / ('docassemble_' + project + '-1.0-py3-none-any.whl'), 'w') as archive:
                for member, data in files.items():
                    if not member.startswith('docassemble/' + project + '/'):
                        continue
                    if location == bundle and member == migration:
                        if defect == 'missing':
                            continue
                        data = b'stale migration'
                    archive.writestr(member, data)
    if location is None:
        result = probe.check_image_schema(expected, runtime, bundles)
        assert result == {name: expected for name in ('runtime', 'core-r1', 'runtime-r1')}
    else:
        with pytest.raises((OSError, ValueError, KeyError)):
            probe.check_image_schema(expected, runtime, bundles)


@pytest.mark.parametrize('columns', [None, [], ['credential_request_binding']])
def test_image_migration_database_schema_probe_is_read_only_and_respects_table_prefix(monkeypatch, columns):
    from sqlalchemy.exc import NoSuchTableError

    probe = load('preparation/image_migration_schema_probe', monkeypatch)
    disposed = []
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))
    monkeypatch.setitem(sys.modules, 'image_bridge_proof', SimpleNamespace(database=lambda: (engine, 'proof_')))
    def get_columns(table):
        assert table == 'proof_managed_candidate'
        if columns is None:
            raise NoSuchTableError(table)
        return [{'name': column} for column in columns]
    monkeypatch.setattr('sqlalchemy.inspect', lambda value: SimpleNamespace(get_columns=get_columns) if value is engine else None)
    if columns:
        assert probe.check_database_schema() == {'table': 'proof_managed_candidate', 'column': 'credential_request_binding'}
    else:
        with pytest.raises(ValueError, match='05b500000001 has not been applied'):
            probe.check_database_schema()
    assert disposed == [True]


def test_outer_preflight_refuses_unattached_volume(monkeypatch, tmp_path):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker(volumes=['da-24-s3-backup'])
    monkeypatch.setattr(module.subprocess, 'run', docker)
    with pytest.raises(SystemExit) as error:
        module.main(['--only', 'restore-s3', '--evidence', str(tmp_path / 'new')])
    assert error.value.code == 2
    assert docker.volumes == {'da-24-s3-backup'}
    assert not any(command[:2] == ['docker', 'run'] for command in docker.commands)


@pytest.mark.parametrize('fail_export,fail_remove,linger', [(True, False, False), (False, True, False), (False, False, True)])
def test_source_export_and_verified_cleanup_fail_the_phase(monkeypatch, tmp_path, fail_export, fail_remove, linger):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker(fail_export=fail_export, fail_remove=fail_remove, linger=linger)
    monkeypatch.setattr(module.subprocess, 'run', docker)
    run = module.Run(tmp_path, 'fixture')
    run.phase('direct_url')
    assert run.results[0]['exit'] != 0
    assert json.loads((tmp_path / 'results.json').read_text())[0]['exit'] != 0
    assert any(command[:2] == ['docker', 'cp'] for command in docker.commands)
    assert any(command[:2] == ['docker', 'rm'] for command in docker.commands)
    assert any(command[:3] == ['docker', 'volume', 'rm'] for command in docker.commands)


@pytest.mark.parametrize('fail_remove,linger', [(True, False), (False, True)])
def test_outer_cleanup_failure_invalidates_success_and_preserves_existing_objects(monkeypatch, tmp_path, fail_remove, linger):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker(containers=['da-existing', 'caddy'], volumes=['da-existing-volume'],
                        fail_remove=fail_remove, linger=linger)
    def execute(argv, **kwargs):
        if argv[0] != 'docker':
            docker.containers.add('da-new')
            docker.volumes.add('da-new-volume')
        return docker(argv, **kwargs)
    monkeypatch.setattr(module.subprocess, 'run', execute)
    run = module.Run(tmp_path, 'fixture')
    run.phase('boot')
    assert run.results[0]['exit'] != 0
    assert 'cleanup_error' in run.results[0]
    removed = [command[-1] for command in docker.commands if command[:2] == ['docker', 'rm'] or
               command[:3] == ['docker', 'volume', 'rm']]
    assert set(removed) == {'da-new', 'da-new-volume'}
    assert {'da-existing', 'caddy'} <= docker.containers
    assert 'da-existing-volume' in docker.volumes


def test_successful_required_export_and_verified_cleanup_pass(monkeypatch, tmp_path):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker()
    monkeypatch.setattr(module.subprocess, 'run', docker)
    run = module.Run(tmp_path, 'fixture')
    run.phase('direct_url')
    assert run.results[0]['exit'] == 0
    assert docker.containers == docker.volumes == set()


@pytest.mark.parametrize('phase', [
    'config_publication', 'zip_install', 'playground_install', 'git_install',
    'private_git', 'private_index_credentials', 'direct_url',
])
def test_source_qualification_owns_database_and_runs_whole_proof_as_worker(monkeypatch, tmp_path, phase):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker()
    environments = {}
    def execute(argv, **kwargs):
        if argv[:2] == ['docker', 'run']:
            environments[argv[argv.index('--name') + 1]] = kwargs['env']
        return docker(argv, **kwargs)
    monkeypatch.setattr(module.subprocess, 'run', execute)
    run = module.Run(tmp_path, 'fixture')
    run.phase(phase)
    assert run.results[0]['exit'] == 0
    launches = [command for command in docker.commands if command[:2] == ['docker', 'run']]
    assert len(launches) == 2
    database, source = launches
    assert database[database.index('--name') + 1] == module.SOURCE_DATABASE
    assert database[-1] == 'postgres:16'
    assert module.SOURCE_DATABASE + ':/var/lib/postgresql/data' in database
    assert source[source.index('--network') + 1] == 'container:' + module.SOURCE_DATABASE
    assert all('-p' not in command and '--publish' not in command and '--add-host' not in command
               for command in launches)
    proof = next(command for command in docker.commands if command[-2:] == [
        '/repo/uv-experiment/preparation/source_qualification_proof.py', phase])
    assert proof[:4] == ['docker', 'exec', '--user', 'da-environment']
    ready = next(command for command in docker.commands if command[-1] == module.SOURCE_DATABASE_READY)
    assert ready[:4] == ['docker', 'exec', '--user', 'da-environment']
    assert 'psycopg2.connect(url, connect_timeout=1)' in ready[-1]
    assert "cursor.execute('SELECT 1')" in ready[-1]
    assert docker.commands.index(database) < docker.commands.index(source) < docker.commands.index(ready) < docker.commands.index(proof)
    assert environments[module.SOURCE_DATABASE]['POSTGRES_PASSWORD'] == run.source_password
    source_name = source[source.index('--name') + 1]
    assert environments[source_name]['DA_PREPARATION_TEST_URL'] == run.source_database_url
    assert '@127.0.0.1:5432/postgres' in run.source_database_url
    assert not docker.containers and not docker.volumes
    assert ['docker', 'volume', 'rm', module.SOURCE_DATABASE] in docker.commands


@pytest.mark.parametrize('kind', ['container', 'volume'])
@pytest.mark.parametrize('name', ['da-24-source-direct-url', 'da-24-source-postgres'])
def test_source_qualification_refuses_preexisting_source_or_database_before_mutation(monkeypatch, tmp_path, kind, name):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker(containers=[name] if kind == 'container' else [],
                        volumes=[name] if kind == 'volume' else [])
    monkeypatch.setattr(module.subprocess, 'run', docker)
    run = module.Run(tmp_path, 'fixture')
    with pytest.raises(RuntimeError, match='pre-existing proof resources'):
        run.disposable_sources('direct_url', tmp_path)
    assert not any(command[:2] in (['docker', 'run'], ['docker', 'rm']) or
                   command[:3] in (['docker', 'volume', 'create'], ['docker', 'volume', 'rm'])
                   for command in docker.commands)
    if kind == 'container':
        assert name in docker.containers
    else:
        assert docker.volume_contents[name] == b'sentinel bytes'


@pytest.mark.parametrize('kind', ['container', 'volume'])
def test_outer_preflight_never_exempts_source_database(monkeypatch, tmp_path, kind):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker(containers=[module.SOURCE_DATABASE] if kind == 'container' else [],
                        volumes=[module.SOURCE_DATABASE] if kind == 'volume' else [])
    monkeypatch.setattr(module.subprocess, 'run', docker)
    with pytest.raises(SystemExit) as error:
        module.main(['--only', 'direct_url', '--evidence', str(tmp_path / 'new')])
    assert error.value.code == 2
    assert not any(command[:2] == ['docker', 'run'] or command[:3] == ['docker', 'volume', 'create']
                   for command in docker.commands)


def test_source_database_is_created_after_outer_preflight(monkeypatch, tmp_path):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker()
    def execute(argv, **kwargs):
        result = docker(argv, **kwargs)
        if argv[:3] == ['docker', 'image', 'inspect']:
            result.stdout = '[]'
        return result
    monkeypatch.setattr(module.subprocess, 'run', execute)
    monkeypatch.setattr(module, 'source_hashes', lambda: {})
    assert module.main(['--only', 'direct_url', '--evidence', str(tmp_path / 'new')]) == 1
    creation = docker.commands.index(['docker', 'volume', 'create', module.SOURCE_DATABASE])
    assert docker.commands.index(['docker', 'ps', '-a', '--format', '{{.Names}}']) < creation
    assert docker.commands.index(['docker', 'volume', 'ls', '-q']) < creation
    assert docker.commands.index(['docker', 'image', 'inspect', 'da-uv-24:local']) < creation
    assert not docker.containers and not docker.volumes


@pytest.mark.parametrize('failure', ['volume-create', 'database-start', 'source-start', 'readiness', 'proof'])
def test_source_database_and_volume_are_cleaned_after_failure(monkeypatch, tmp_path, failure):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker()
    def execute(argv, **kwargs):
        result = docker(argv, **kwargs)
        is_volume = argv[:3] == ['docker', 'volume', 'create']
        is_database = argv[:2] == ['docker', 'run'] and module.SOURCE_DATABASE in argv
        is_source = argv[:2] == ['docker', 'run'] and 'da-24-source-direct-url' in argv
        is_ready = argv[-1] == module.SOURCE_DATABASE_READY
        is_proof = argv[-2:] == ['/repo/uv-experiment/preparation/source_qualification_proof.py', 'direct_url']
        if {'volume-create': is_volume, 'database-start': is_database, 'source-start': is_source,
            'readiness': is_ready, 'proof': is_proof}[failure]:
            result.returncode, result.stderr = 1, 'injected ' + failure
        return result
    monkeypatch.setattr(module.subprocess, 'run', execute)
    run = module.Run(tmp_path, 'fixture')
    run.phase('direct_url')
    assert run.results[0]['exit'] != 0
    assert not docker.containers and not docker.volumes
    assert ['docker', 'volume', 'rm', module.SOURCE_DATABASE] in docker.commands
    if failure == 'readiness':
        assert not any(command[-2:] == ['/repo/uv-experiment/preparation/source_qualification_proof.py', 'direct_url']
                       for command in docker.commands)


def test_source_setup_failure_is_retained_before_missing_inventory_export(monkeypatch, tmp_path):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker(fail_export=True)
    def execute(argv, **kwargs):
        if argv[:2] == ['docker', 'cp']:
            assert (tmp_path / 'config_publication/driver.log').read_text() == 'proof stdoutPostgreSQL: No route to host'
        result = docker(argv, **kwargs)
        if argv[-2:] == ['/repo/uv-experiment/preparation/source_qualification_proof.py', 'config_publication']:
            result.returncode, result.stdout, result.stderr = 1, 'proof stdout', 'PostgreSQL: No route to host'
        if argv[:2] == ['docker', 'cp']:
            result.stderr = 'Could not find qualification-inventories.json'
        return result
    monkeypatch.setattr(module.subprocess, 'run', execute)
    run = module.Run(tmp_path, 'fixture')
    run.phase('config_publication')
    assert run.results[0]['exit'] != 0
    assert (tmp_path / 'config_publication/driver.log').read_text() == 'proof stdoutPostgreSQL: No route to host'
    assert 'Could not find qualification-inventories.json' in (tmp_path / 'config_publication/driver-error.txt').read_text()
    assert not docker.containers and not docker.volumes


def test_source_export_and_cleanup_failures_preserve_original_proof_diagnostic(monkeypatch, tmp_path):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker(fail_export=True, fail_remove=True)
    def execute(argv, **kwargs):
        result = docker(argv, **kwargs)
        if argv[-2:] == ['/repo/uv-experiment/preparation/source_qualification_proof.py', 'direct_url']:
            result.returncode, result.stderr = 1, 'original proof failure'
        if argv[:2] == ['docker', 'cp']:
            result.stderr = 'missing mandatory inventory'
        return result
    monkeypatch.setattr(module.subprocess, 'run', execute)
    run = module.Run(tmp_path, 'fixture')
    run.phase('direct_url')
    assert run.results[0]['exit'] != 0
    assert (tmp_path / 'direct_url/driver.log').read_text() == 'original proof failure'
    error = (tmp_path / 'direct_url/driver-error.txt').read_text()
    assert 'missing mandatory inventory' in error and 'proof cleanup failed' in error
    assert 'cleanup_error' in run.results[0]


@pytest.mark.parametrize('failure', ['proof', 'readiness', 'timeout', 'exception'])
def test_source_database_credentials_never_enter_retained_commands_or_output(monkeypatch, tmp_path, failure):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker()
    run = module.Run(tmp_path, 'fixture')
    def execute(argv, **kwargs):
        result = docker(argv, **kwargs)
        is_proof = argv[-2:] == ['/repo/uv-experiment/preparation/source_qualification_proof.py', 'direct_url']
        is_ready = argv[-1] == module.SOURCE_DATABASE_READY
        if (failure in ('proof', 'timeout', 'exception') and is_proof) or (failure == 'readiness' and is_ready):
            if failure == 'timeout':
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'], output=run.source_password.encode(),
                                                stderr=run.source_database_url.encode())
            if failure == 'exception':
                raise RuntimeError(run.source_password + ' ' + run.source_database_url)
            result.returncode, result.stdout, result.stderr = 1, run.source_password, run.source_database_url
        return result
    monkeypatch.setattr(module.subprocess, 'run', execute)
    run.phase('direct_url')
    assert run.results[0]['exit'] != 0
    assert run.source_password not in json.dumps(docker.commands)
    assert run.source_database_url not in json.dumps(docker.commands)
    evidence = '\n'.join(path.read_text() for path in tmp_path.rglob('*') if path.is_file())
    assert run.source_password not in evidence and run.source_database_url not in evidence
    assert '[source database password]' in evidence and '[source database URL]' in evidence
    assert not docker.containers and not docker.volumes


@pytest.mark.parametrize('fail_export,fail_remove,linger', [(True, False, False), (False, True, False), (False, False, True)])
def test_checkpoint_driver_requires_exports_and_cleanup(monkeypatch, tmp_path, fail_export, fail_remove, linger):
    module = load('run-checkpoint-restore-proof', monkeypatch)
    monkeypatch.setenv('PROOF_EVIDENCE_DIR', str(tmp_path))
    monkeypatch.setenv('PROOF_CHECKPOINT_PROVIDER', 'local')
    docker = FakeDocker(fail_export=fail_export, fail_remove=fail_remove, linger=linger)
    # Only final required exports fail, not the earlier baseline transfer.
    def execute(argv, **kwargs):
        required = argv[:2] == ['docker', 'cp'] and argv[2].endswith((
            'cold-boot.log', 'docassemble-uv-invocations.log', 'result.json'))
        docker.fail_export = fail_export and required
        return docker(argv, **kwargs)
    monkeypatch.setattr(module.subprocess, 'run', execute)
    with pytest.raises(RuntimeError, match='export failed|cleanup failed'):
        module.main()
    assert any(command[:2] == ['docker', 'rm'] for command in docker.commands)
    assert any(command[:3] == ['docker', 'volume', 'rm'] for command in docker.commands)


def test_timeout_persists_start_and_partial_output_before_teardown(monkeypatch, tmp_path):
    module = load('run-qualification', monkeypatch)
    docker = FakeDocker()
    def execute(argv, **kwargs):
        if argv[0] != 'docker':
            records = json.loads((tmp_path / 'commands.json').read_text())
            assert records[-1]['argv'] == argv and records[-1]['status'] == 'running'
            docker.containers.add('da-timeout')
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'], output=b'partial stdout', stderr=b'partial stderr')
        if argv[:2] == ['docker', 'rm']:
            assert (tmp_path / 'boot/driver.log').read_text() == 'partial stdoutpartial stderr'
            assert (tmp_path / 'command-0002.stdout').read_text() == 'partial stdout'
            assert (tmp_path / 'command-0002.stderr').read_text() == 'partial stderr'
            timed_out = json.loads((tmp_path / 'commands.json').read_text())[2]
            assert timed_out['status'] == 'timeout' and timed_out['exit'] == -1
            assert timed_out['seconds'] >= 0 and timed_out['started_at']
        return docker(argv, **kwargs)
    monkeypatch.setattr(module.subprocess, 'run', execute)
    run = module.Run(tmp_path, 'fixture')
    run.phase('boot')
    assert run.results[0]['exit'] == -1
    assert not docker.containers


def test_scenario_start_and_failure_cleanup_never_remove_preexisting_container(monkeypatch, tmp_path):
    module = load('scenarios/harness', monkeypatch)
    docker = module.Docker(module.Evidence(tmp_path))
    fake = FakeDocker(containers=['da-sentinel'])
    monkeypatch.setattr(module.subprocess, 'run', fake)
    with pytest.raises(module.Failure, match='pre-existing'):
        docker.start('da-sentinel', 'fixture')
    docker.rm('da-sentinel')  # A caller's finally block cannot delete the collision.
    assert fake.containers == {'da-sentinel'}
    assert not any(command[:2] == ['docker', 'rm'] for command in fake.commands)


def test_scenario_can_replace_and_verify_cleanup_of_its_own_container(monkeypatch, tmp_path):
    module = load('scenarios/harness', monkeypatch)
    docker = module.Docker(module.Evidence(tmp_path))
    fake = FakeDocker()
    monkeypatch.setattr(module.subprocess, 'run', fake)
    docker.start('da-owned', 'fixture')
    docker.start('da-owned', 'fixture')
    fake.linger = True
    with pytest.raises(module.Failure, match='cleanup failed'):
        docker.rm('da-owned')
