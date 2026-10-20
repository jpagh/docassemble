"""Independent cold-restore authorization, files boundary and schema reuse."""
import io
import json
import os
from pathlib import Path
import tarfile
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

from docassemble_executor.cold_restore import authorization, ColdRestoreError, restore_files
from docassemble_executor.cli import build_parser
from docassemble_executor.managed_backup import _sha
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.startup import managed_requested, StartupError
from test_selected_baseline import upgrade  # noqa: F401


@pytest.fixture
def auth(tmp_path, monkeypatch):
    dump = tmp_path / 'database'
    dump.write_bytes(b'dump')
    root = tmp_path / 'authorization'
    root.mkdir(mode=0o700)
    path = root / 'restore.json'
    value = {'format': 1, 'dump_sha256': _sha(dump), 'incarnation': str(uuid.uuid4()), 'old_deployment_excluded': True}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    if os.getuid() != 0:
        # Native act runs root; non-root local tests can still validate parsing
        # without pretending root restriction is absent in production.
        original = os.stat_result
        real_fstat = os.fstat
        def fstat(fd):
            info = list(real_fstat(fd))
            info[4] = 0
            return original(info)
        monkeypatch.setattr(os, 'fstat', fstat)
        real_stat = type(path).stat
        def pathstat(self, *args, **kwargs):
            info = list(real_stat(self, *args, **kwargs))
            info[4] = 0
            return original(info)
        monkeypatch.setattr(type(path), 'stat', pathstat)
    return path, dump, value


def test_explicit_root_authority_binds_exact_dump_and_incarnation(auth):
    path, dump, value = auth
    assert authorization(path, dump) == {key: value[key] for key in ('dump_sha256', 'incarnation')}


@pytest.mark.parametrize('change', ['no-exclusion', 'wrong-dump', 'bad-incarnation', 'file-mode', 'directory-mode', 'symlink'])
def test_no_copied_health_or_expired_lease_can_replace_fence(auth, change):
    path, dump, value = auth
    value.update(host='copied-host', pid=os.getpid(), heartbeat='fresh', applied=True, lease_expired=True)
    if change == 'no-exclusion':
        value['old_deployment_excluded'] = False
    elif change == 'wrong-dump':
        value['dump_sha256'] = '0' * 64
    elif change == 'bad-incarnation':
        value['incarnation'] = 'copied-host'
    path.write_text(json.dumps(value))
    if change == 'file-mode':
        path.chmod(0o644)
    elif change == 'directory-mode':
        path.parent.chmod(0o755)
    elif change == 'symlink':
        other = path.with_suffix('.source')
        path.rename(other)
        path.symlink_to(other)
    with pytest.raises((ColdRestoreError, ValueError)):
        authorization(path, dump)


def test_restore_cli_is_explicit_and_precedes_boot_selection():
    args = build_parser().parse_args(['restore', '--dump', 'db', '--files', 'files.tar', '--authorization', 'auth'])
    assert args.command == 'restore'


def test_disabled_managed_boot_refuses_cold_restore_evidence(tmp_path):
    (tmp_path / 'cold-restore.json').write_text('{}')
    with pytest.raises(StartupError, match='disabled'):
        managed_requested(False, tmp_path)


@pytest.mark.parametrize('bad', ['../escape', '/absolute', 'files/link', 'config.yml/nested', 'runtime/bin/python'])
def test_archive_path_or_link_cannot_escape_existing_backup_layout(tmp_path, bad):
    archive = tmp_path / 'files.tar'
    with tarfile.open(archive, 'w') as output:
        info = tarfile.TarInfo(bad)
        if bad == 'files/link':
            info.type = tarfile.SYMTYPE
            info.linkname = '/etc/shadow'
        output.addfile(info)
    paths = replace(ExecutorPaths.from_env(), da_root=tmp_path / 'root', config_file=tmp_path / 'root/config/config.yml')
    with pytest.raises(ColdRestoreError):
        restore_files(archive, paths)
    assert not paths.config_file.exists()


def test_configuration_and_files_restore_without_any_application_python(tmp_path):
    archive = tmp_path / 'files.tar'
    with tarfile.open(archive, 'w') as output:
        for name, data in [('config.yml', b'db:\n  name: restored\n'), ('files/fixture', b'interview fixture')]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            info.uid, info.gid = os.getuid(), os.getgid()
            output.addfile(info, io.BytesIO(data))
    paths = replace(ExecutorPaths.from_env(), da_root=tmp_path / 'root', config_file=tmp_path / 'root/config/config.yml')
    restore_files(archive, paths)
    assert paths.config_file.read_bytes() == b'db:\n  name: restored\n'
    assert (paths.da_root / 'files/fixture').read_bytes() == b'interview fixture'


def test_cold_restore_gates_preserve_promoted_recovery_instead_of_image_defaults(upgrade):  # noqa: F811
    from docassemble_executor.selected_baseline import retain_selection, promote_recovery, load_baselines
    poller, claim, _ = upgrade
    retain_selection(poller.paths, claim.snapshot, poller.store)
    promote_recovery(poller.paths, claim.snapshot)
    before = load_baselines(poller.paths)
    assert before['recovery_baseline'] == claim.snapshot['selected_baseline']
    assert before['recovery_baseline'] != claim.snapshot['recovery_baseline']
    poller._gates(replace(claim, cold_restore=True))
    assert load_baselines(poller.paths) == before


def test_restored_successful_schema_uses_readonly_check_never_setup(tmp_path, monkeypatch):
    from docassemble_executor.journal import Journal
    import docassemble_executor.published as published
    from test_schema_journal import _prepare_paths, _publish_claim, _publish_context
    paths, claim = _prepare_paths(tmp_path), _publish_claim()
    claim.restore_schema = {'revision': claim.revision_id, 'result': 'succeeded', 'completed_at': 'historical-success'}
    monkeypatch.setattr(published, 'load_context', lambda *_args, **_kwargs: _publish_context())
    def forbidden(*_):
        raise AssertionError('successful setup must not rerun after restore')
    monkeypatch.setattr(published, '_create_tables', forbidden)
    monkeypatch.setattr(published, 'check_schema', lambda *_: SimpleNamespace(usable=True, database_revision='fixture_revision'))
    monkeypatch.setattr(published, 'probe_runtime', lambda *_: SimpleNamespace(ok=True))
    monkeypatch.setattr(published, '_start_web', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(published, 'prepare_roles', lambda _: [SimpleNamespace(name=name, ok=True) for name in
                       published.RUNTIME_ROLES + published.INFRASTRUCTURE_ROLES])
    result = published.prepare_published(paths, claim)
    assert result['schema_verified']
    assert Journal.load(paths.journal_file).schema_succeeded


def test_restore_objects_never_overwrites_image_owned_material(tmp_path):
    # A different-target restore must preserve the replacement image's identity;
    # image-owned backup objects are never copied over it (migration is ticket 19).
    import os
    from docassemble_executor import cold_restore
    objects = tmp_path / 'objects'
    (objects / 'image').mkdir(parents=True)
    (objects / 'image' / 'manifest.json').write_text('backup-image')
    (objects / 'image-baseline').mkdir()
    (objects / 'image-baseline' / 'baseline.json').write_text('backup-baseline')
    (objects / 'material').mkdir()
    (objects / 'material' / 'x').write_text('material-bytes')
    manifest, baseline = tmp_path / 'manifest.json', tmp_path / 'baseline.json'
    manifest.write_text('replacement-image')
    baseline.write_text('replacement-baseline')
    material = tmp_path / 'material-x'
    material.write_text('old')
    info = {'mode': 0o644, 'uid': os.getuid(), 'gid': os.getgid()}
    document = {
        'directories': {str(tmp_path): {'mode': 0o755, 'uid': os.getuid(), 'gid': os.getgid()}},
        'files': {
            'image/manifest.json': {'source': str(manifest), **info},
            'image-baseline/baseline.json': {'source': str(baseline), **info},
            'material/x': {'source': str(material), **info}}}
    cold_restore.restore_objects(tmp_path, document, {'unavailable_objects': []})
    assert manifest.read_text() == 'replacement-image'
    assert baseline.read_text() == 'replacement-baseline'
    assert material.read_text() == 'material-bytes'


def test_checkpoint_completion_fsync_skips_image_owned_directories(tmp_path):
    # restore_objects never creates image-owned directories, so the checkpoint
    # completion fsync cannot require them: the operator may provision the bridge
    # after the restore, and a missing restored directory must still fail loudly.
    import os
    from docassemble_executor.cold_restore import image_owned_directories
    info = {'mode': 0o644, 'uid': os.getuid(), 'gid': os.getgid()}
    absent = tmp_path / 'qualified-image'
    restored = tmp_path / 'managed'
    restored.mkdir()
    document = {
        'directories': {str(absent): {'mode': 0o755, 'uid': os.getuid(), 'gid': os.getgid()},
                        str(restored): {'mode': 0o755, 'uid': os.getuid(), 'gid': os.getgid()}},
        'files': {'image-baseline/baseline.json': {'source': str(absent / 'baseline.json'), **info},
                  'state/lifecycle.json': {'source': str(restored / 'lifecycle.json'), **info}}}
    owned = image_owned_directories(document)
    assert absent in owned and not absent.exists()
    assert restored not in owned and restored.exists()
    source = (Path(__file__).resolve().parents[1] / 'src/docassemble_executor/checkpoint_restore.py').read_text()
    assert 'image_owned = image_owned_directories(restored_document)' in source
    assert source.index('if target in image_owned:') > source.index('image_owned = image_owned_directories')
