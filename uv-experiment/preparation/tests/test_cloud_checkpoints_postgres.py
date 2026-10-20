"""Real dump binding with external cloud CLI fakes (never live S3/Azure)."""
# ruff: noqa: F811
import json
from pathlib import Path
import sys

import pytest

from test_preparation_postgres import protocol  # noqa: F401
from test_managed_backup_postgres import objects  # noqa: F401
from test_checkpoints_postgres import setup_checkpoint
from docassemble_executor.checkpoints import capture, verify_checkpoint
from docassemble_executor.cloud_checkpoints import CloudCheckpoint
from docassemble_executor.managed_backup import ManagedBackupError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'executor/tests'))
from test_cloud_checkpoints import cloud_fixture  # noqa: E402


def setup_cloud_checkpoint(protocol, objects, monkeypatch, inputs):
    kwargs, claim = setup_checkpoint(protocol, objects, monkeypatch)
    cloud_config = ({'s3': {'bucket': inputs['S3BUCKET']}} if inputs['S3ENABLE'] == 'true' else
                    {'azure': {'container': inputs['AZURECONTAINER'], 'account name': inputs['AZURE_STORAGE_ACCOUNT']}})
    # JSON is also valid YAML; keep actual config and the trusted resolved
    # mapping consistent, just as the installation caller must.
    kwargs['paths'].config_file.write_text(json.dumps(dict(cloud_config, db={},
        uploads=str(kwargs['paths'].da_root / 'files'))))
    kwargs.pop('prefix')
    return kwargs, claim


@pytest.fixture(params=['s3', 'azure'])
def provider_inputs(request, tmp_path):
    return cloud_fixture(tmp_path, request.param)


def test_cloud_checkpoint_retrieval_integrity_after_live_changes_and_nightly_rotation(protocol, objects, monkeypatch, provider_inputs, tmp_path):
    inputs, bucket = provider_inputs
    kwargs, claim = setup_cloud_checkpoint(protocol, objects, monkeypatch, inputs)
    first = capture(protocol, claim=claim, inputs=inputs, **kwargs)
    second = capture(protocol, claim=claim, inputs=inputs, **kwargs)
    assert first['id'] != second['id'] and first['complete'] and second['complete']
    assert first['destination']['prefix'] != second['destination']['prefix']
    # Run the actual nightly backup operation with its overwrite/delete flags;
    # only the provider transport is faked, not the rotation/destination logic.
    import subprocess
    paths = kwargs['paths']
    (paths.da_root / 'log').mkdir()
    nightly_env = dict(inputs, DA_ROOT=str(paths.da_root), DA_CONFIG_FILE=str(paths.config_file),
                       CONTAINERROLE='web', DA_UV_MANAGED='false', DABACKUPDAYS='1')
    script = Path(__file__).resolve().parents[3] / 'Docker/backup.sh'
    for _ in range(2):
        nightly = subprocess.run(['bash', str(script)], env=nightly_env, capture_output=True, text=True)
        assert nightly.returncode == 0, nightly.stderr
    (bucket / 'files/1/interview').write_bytes(b'post-upgrade progress now differs')
    for index, status in enumerate((first, second)):
        destination = tmp_path / ('retrieved-' + str(index))
        manifest = CloudCheckpoint(inputs).retrieve(status['destination']['prefix'], destination, 'test_')
        row = manifest['cloud']['files']['files/1/interview']
        assert (destination / 'objects' / row['file']).read_bytes() == b'pre-change interview bytes'
        assert manifest['retention'] == 'operator-pruned-no-auto-deletion'
        assert manifest['restore']['desired_revision_id'] == 'retained'
        assert manifest['desired_revision_id'] == 'advanced-desired'
        assert not manifest['known_good'] and status['created_at'] == manifest['created_at']
        assert manifest == verify_checkpoint(destination, 'test_')
    # The unique prefix cannot be reused by another upload, even with identical bytes.
    directory = next((kwargs['paths'].da_root / 'backup/checkpoints').iterdir())
    (directory / 'checkpoint.pending').write_bytes((directory / 'checkpoint.json').read_bytes())
    with pytest.raises(ManagedBackupError, match='already exists'):
        CloudCheckpoint(inputs).publish(directory, first['destination']['prefix'], 'test_')


@pytest.mark.parametrize('failure', ['interrupted-upload', 'missing-object', 'changed-live', 'partial-upload', 'corrupt-upload', 'auth'])
def test_cloud_failure_never_advertises_complete_checkpoint(protocol, objects, monkeypatch, provider_inputs, failure):
    inputs, bucket = provider_inputs
    kwargs, claim = setup_cloud_checkpoint(protocol, objects, monkeypatch, inputs)
    inputs['CLOUD_FAKE_FAILURE'] = failure
    with pytest.raises((ManagedBackupError, OSError)) as error:
        capture(protocol, claim=claim, inputs=inputs, **kwargs)
    assert 'DISPOSABLE' not in str(error.value)
    assert not list((kwargs['paths'].da_root / 'backup/checkpoints').rglob('checkpoint.json'))
    assert not list((bucket / 'checkpoints').rglob('checkpoint.json'))
    status = json.loads((kwargs['paths'].state_dir / 'checkpoints' / (claim.attempt_id + '.json')).read_text())
    assert not status['complete'] and not status['known_good'] and status['state'] == 'failed-incomplete'
    assert (kwargs['paths'].da_root / 'files/interview').read_text() == 'pre-change progress'


@pytest.mark.parametrize('missing', ['cloud-content', 'managed-object', 'database', 'cloud-manifest-row', 'identity'])
def test_remote_missing_or_partial_checkpoint_fails_retrieval(protocol, objects, monkeypatch, provider_inputs, tmp_path, missing):
    inputs, bucket = provider_inputs
    kwargs, claim = setup_cloud_checkpoint(protocol, objects, monkeypatch, inputs)
    status = capture(protocol, claim=claim, inputs=inputs, **kwargs)
    remote = bucket / status['destination']['prefix']
    if missing == 'cloud-content':
        next((remote / 'objects/cloud').iterdir()).unlink()
    elif missing == 'managed-object':
        next(p for p in (remote / 'database.managed/objects').rglob('*') if p.is_file()).unlink()
    elif missing == 'database':
        (remote / 'database').unlink()
    else:
        document = json.loads((remote / 'checkpoint.json').read_text())
        if missing == 'identity':
            import uuid
            document['id'] = str(uuid.uuid4())
        else:
            document['cloud']['files'].pop(next(iter(document['cloud']['files'])))
        (remote / 'checkpoint.json').write_text(json.dumps(document))
    with pytest.raises((ManagedBackupError, OSError)):
        CloudCheckpoint(inputs).retrieve(status['destination']['prefix'], tmp_path / 'retrieved', 'test_')
