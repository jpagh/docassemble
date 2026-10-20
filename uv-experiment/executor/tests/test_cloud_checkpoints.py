"""Cloud checkpoints use external provider CLI fakes, not live S3/Azure."""
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from docassemble_executor.cloud_checkpoints import CloudCheckpoint, verify_cloud_files
from docassemble_executor.managed_backup import ObjectCopy, ManagedBackupError


FAKE = Path(__file__).resolve().parents[2] / 'backup/cloud_cli_fake.py'


def cloud_fixture(tmp_path, provider):
    tools = tmp_path / 'bin'
    tools.mkdir()
    for name in ('s4cmd', 'az'):
        (tools / name).write_bytes(FAKE.read_bytes())
        (tools / name).chmod(0o755)
    inputs = {'PATH': str(tools) + os.pathsep + os.environ['PATH'], 'CLOUD_FAKE_ROOT': str(tmp_path / 'remote'),
              'S3ENABLE': 'true' if provider == 's3' else 'false',
              'AZUREENABLE': 'true' if provider == 'azure' else 'false',
              'S3BUCKET': 'fixture', 'AZURECONTAINER': 'fixture', 'AZURE_STORAGE_ACCOUNT': 'fixture-account',
              'AWS_SECRET_ACCESS_KEY': 'DISPOSABLE-S3-SECRET', 'AZURE_STORAGE_KEY': 'DISPOSABLE-AZURE-SECRET'}
    bucket = tmp_path / 'remote/fixture'
    (bucket / 'files/1').mkdir(parents=True)
    (bucket / 'files/1/interview').write_bytes(b'pre-change interview bytes')
    (bucket / 'custom section').mkdir()
    (bucket / 'custom section/custom object').write_bytes(b'custom data')
    (bucket / 'backup/10-16').mkdir(parents=True)
    (bucket / 'backup/10-16/old').write_text('nightly rotation is not live data')
    return inputs, bucket


@pytest.fixture(params=['s3', 'azure'])
def cloud(request, tmp_path):
    return cloud_fixture(tmp_path, request.param)


def test_capture_reads_actual_content_and_excludes_nightly_objects(cloud, tmp_path):
    inputs, bucket = cloud
    provider = CloudCheckpoint(inputs)
    copied = ObjectCopy(tmp_path / 'capture')
    manifest = provider.capture(copied)
    provider.verify_live(manifest)
    assert set(manifest['files']) == {'files/1/interview', 'custom section/custom object'}
    document = {'cloud': manifest, 'files': copied.files}
    verify_cloud_files(document)
    for key, row in manifest['files'].items():
        assert (tmp_path / 'capture/objects' / row['file']).read_bytes() == (bucket / key).read_bytes()
        assert row['sha256'] == hashlib.sha256((bucket / key).read_bytes()).hexdigest()
    assert all(not source.startswith('/tmp/') for source in copied.directories)


@pytest.mark.parametrize('failure', ['missing-object', 'changed-live', 'auth'])
def test_cloud_capture_failure_is_explicit_and_authentication_is_redacted(cloud, tmp_path, failure, capsys):
    inputs, _ = cloud
    inputs['CLOUD_FAKE_FAILURE'] = failure
    provider = CloudCheckpoint(inputs)
    with pytest.raises(ManagedBackupError) as error:
        manifest = provider.capture(ObjectCopy(tmp_path / 'capture'))
        provider.verify_live(manifest)
    messages = str(error.value) + repr(error.value) + str(capsys.readouterr())
    assert 'DISPOSABLE' not in messages and 'credential-bearing' not in messages


@pytest.mark.parametrize('part', ['files', 'listing', 'binding', 'content-object', 'all-cloud-metadata'])
def test_partial_cloud_manifest_cannot_qualify_coverage(cloud, tmp_path, part):
    inputs, _ = cloud
    copied = ObjectCopy(tmp_path / 'capture')
    manifest = CloudCheckpoint(inputs).capture(copied)
    document = copy.deepcopy({'cloud': manifest, 'files': copied.files})
    key = next(iter(manifest['files']))
    if part in ('files', 'listing'):
        document['cloud'][part].pop(key)
    elif part == 'binding':
        document['cloud']['files'][key]['sha256'] = '0' * 64
    elif part == 'content-object':
        document['files'].pop(manifest['files'][key]['file'])
    else:
        document.pop('cloud')
    with pytest.raises(ManagedBackupError):
        verify_cloud_files(document)


@pytest.mark.parametrize('name', ['file\nname', 'file*name', 'file?name', 'file[name', 'trailing '])
def test_ambiguous_cli_object_names_fail_instead_of_omitting(cloud, name):
    inputs, bucket = cloud
    (bucket / name).write_text('cannot safely certify this key')
    with pytest.raises(ManagedBackupError):
        CloudCheckpoint(inputs).live_listing()


def test_listing_requires_parseable_full_provider_response(cloud, monkeypatch):
    inputs, _ = cloud
    provider = CloudCheckpoint(inputs)
    monkeypatch.setattr(provider, '_run', lambda _: 'not a certified listing')
    with pytest.raises(ManagedBackupError, match='listing cannot be verified'):
        provider.listing()


def test_retrieval_rejects_remote_path_traversal(cloud, monkeypatch, tmp_path):
    inputs, _ = cloud
    provider = CloudCheckpoint(inputs)
    prefix = 'checkpoints/' + str(uuid.uuid4()) + '-' + str(uuid.uuid4()) + '/'
    monkeypatch.setattr(provider, 'listing', lambda _: {prefix + '../outside': {}})
    with pytest.raises(ManagedBackupError, match='unsafe'):
        provider.retrieve(prefix, tmp_path / 'retrieved', '')


def test_s3_existing_endpoint_and_encryption_inputs_are_preserved_without_shell_options(tmp_path):
    inputs, _ = cloud_fixture(tmp_path, 's3')
    inputs.update(S3ENDPOINTURL='https://fixture.invalid', S3_SSE_ALGORITHM='aws:kms',
                  S3_SSE_KMS_KEY_ID='fixture-key',
                  S4CMD_OPTS='--endpoint-url="https://fixture.invalid" --API-ServerSideEncryption="aws:kms" --API-SSEKMSKeyId="fixture-key"')
    provider = CloudCheckpoint(inputs)
    import shlex
    assert shlex.split(provider.env['S4CMD_OPTS']) == [
        '--endpoint-url=https://fixture.invalid', '--API-ServerSideEncryption=aws:kms', '--API-SSEKMSKeyId=fixture-key']
    assert provider._s3('ls', 's3://fixture/') == ['s4cmd', 'ls', 's3://fixture/']
    assert provider.env['AWS_SECRET_ACCESS_KEY'] == inputs['AWS_SECRET_ACCESS_KEY']
    inputs['S4CMD_OPTS'] = '--API-ACL=unverified-custom-policy'
    with pytest.raises(ManagedBackupError, match='CLI options unverified'):
        CloudCheckpoint(inputs)


def test_cloud_authentication_mapping_is_not_written_to_manifests(cloud, tmp_path):
    inputs, _ = cloud
    manifest = CloudCheckpoint(inputs).capture(ObjectCopy(tmp_path / 'capture'))
    assert 'DISPOSABLE' not in json.dumps(manifest)


@pytest.mark.parametrize('change', ['added', 'deleted'])
def test_changed_live_coverage_fails_instead_of_accepting_partial_manifest(cloud, tmp_path, change):
    inputs, bucket = cloud
    provider = CloudCheckpoint(inputs)
    manifest = provider.capture(ObjectCopy(tmp_path / 'capture'))
    if change == 'added':
        (bucket / 'files/new').write_bytes(b'new upload')
    else:
        (bucket / 'files/1/interview').unlink()
    with pytest.raises(ManagedBackupError, match='coverage changed'):
        provider.verify_live(manifest)


def test_missing_provider_cli_fails_explicitly_without_fallback(cloud, tmp_path):
    inputs, _ = cloud
    inputs['PATH'] = str(tmp_path / 'missing-tools')
    with pytest.raises(ManagedBackupError, match='CLI unavailable'):
        CloudCheckpoint(inputs).capture(ObjectCopy(tmp_path / 'capture'))


def test_timeout_diagnostics_do_not_expose_command_or_credentials(cloud, monkeypatch):
    import subprocess
    import traceback
    inputs, _ = cloud
    provider = CloudCheckpoint(inputs)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(['provider', 'DISPOSABLE-SECRET'], 600, stderr='DISPOSABLE-SECRET')
    monkeypatch.setattr(subprocess, 'run', timeout)
    try:
        provider.listing()
        pytest.fail('timeout was ignored')
    except ManagedBackupError:
        assert 'DISPOSABLE-SECRET' not in traceback.format_exc()


@pytest.mark.parametrize('config,inputs', [
    ({'s3': {'bucket': 'live', 'endpoint url': 'https://live.invalid'}},
     {'S3ENABLE': 'true', 'S3BUCKET': 'live', 'S3ENDPOINTURL': 'https://other.invalid'}),
    ({'s3': {'bucket': 'live', 'server side encryption': {'algorithm': 'aws:kms'}}},
     {'S3ENABLE': 'true', 'S3BUCKET': 'live'}),
    ({'azure': {'container': 'live', 'account name': 'real-account'}},
     {'AZUREENABLE': 'true', 'AZURECONTAINER': 'live', 'AZURE_STORAGE_ACCOUNT': 'other-account'}),
    ({'azure': {'container': 'live', 'account name': 'account', 'endpoint suffix': 'unverified.invalid'}},
     {'AZUREENABLE': 'true', 'AZURECONTAINER': 'live', 'AZURE_STORAGE_ACCOUNT': 'account'}),
    # A connection string overrides the checked account name: reject it when the
    # active configuration did not specify one.
    ({'azure': {'container': 'live', 'account name': 'account'}},
     {'AZUREENABLE': 'true', 'AZURECONTAINER': 'live', 'AZURE_STORAGE_ACCOUNT': 'account',
      'AZURE_STORAGE_CONNECTION_STRING': 'DefaultEndpointsProtocol=https;AccountName=other;AccountKey=DISPOSABLE'}),
])
def test_stale_destination_or_security_mapping_fails_before_cloud_capture(config, inputs):
    from docassemble_executor.checkpoints import require_complete_backup
    with pytest.raises(ManagedBackupError, match='configuration'):
        require_complete_backup(inputs, config)


def test_retrieval_never_writes_through_destination_symlink(cloud, tmp_path):
    inputs, _ = cloud
    actual = tmp_path / 'actual'
    actual.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(actual, target_is_directory=True)
    prefix = 'checkpoints/' + str(uuid.uuid4()) + '-' + str(uuid.uuid4()) + '/'
    with pytest.raises(ManagedBackupError, match='symlink refused'):
        CloudCheckpoint(inputs).retrieve(prefix, link / 'retrieved', '')
    assert not list(actual.iterdir())


def test_cloud_mapping_cannot_reclassify_actual_local_storage(cloud):
    from docassemble_executor.checkpoints import require_complete_backup
    inputs, _ = cloud
    require_complete_backup(inputs, None)  # Trusted-mapping syntax check only.
    with pytest.raises(ManagedBackupError, match='configuration mismatch'):
        require_complete_backup(inputs, {'db': {}})
