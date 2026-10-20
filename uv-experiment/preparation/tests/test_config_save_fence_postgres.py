"""R5: durable save fence at capture/approval and real serialized writer failures."""
# ruff: noqa: F811 -- imported PostgreSQL fixtures are injected by name.
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import hashlib
import multiprocessing
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select, update

from docassemble.base.managed_preparation import (
    Preparation, PreparationError, config_save_status, UNRESOLVED_CONFIG_SAVE)
from docassemble.base.managed_rollback import result_for
from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, adapter, intent  # noqa: F401
from test_historical_rollback_postgres import history
from test_core_upgrade_postgres import upgrade_result
from test_config_import_postgres import config_result
from test_config_observation_postgres import config_view, flashes


VALID_MARKER = {'id': str(uuid.uuid4()), 'actor': '2',
                'created_at': '2026-01-01T00:00:00+00:00', 'write_digest': 'a' * 64}
MARKERS = [None, False, {}, [], 'SECRET malformed marker', VALID_MARKER,
           {**VALID_MARKER, 'id': 'SECRET invalid ID'},
           {**VALID_MARKER, 'actor': 'SECRET\nactor'},
           {**VALID_MARKER, 'created_at': 'SECRET invalid timestamp'},
           {**VALID_MARKER, 'write_digest': 'SECRET invalid digest'}]
OPERATIONS = ['add', 'update', 'remove', 'config', 'repair', 'upgrade', 'rollback']


def rows(service, name):
    with service.engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(service.tables[name])).mappings()]


def whole_state(service):
    return {name: rows(service, name) for name in service.tables}, intent(service)


def set_marker(service, marker):
    with service.authority_write() as connection:
        table = service.tables['environment_state']
        state = connection.scalar(select(table.c.snapshot))
        state[UNRESOLVED_CONFIG_SAVE] = deepcopy(marker)
        connection.execute(update(table).values(snapshot=state))


def operation_inputs(service, operation):
    actor, declaration, kwargs = '2', None, {'operation': operation}
    if operation == 'add':
        declaration = 'Example>=2'
    elif operation in ('update', 'remove', 'rollback'):
        revision = history(service)
        if operation == 'rollback':
            kwargs['rollback_revision'] = revision
        else:
            actor, declaration = '1', 'Example'
            if operation == 'remove':
                kwargs['contribution_id'] = next(row['id'] for row in rows(service, 'requirement_contribution')
                    if row['origin'] == 'interactive' and row['active'])
    elif operation == 'config':
        service.observe_config({'python packages': ['Example>=2']}, environ={})
    elif operation == 'repair':
        with service.authority_write() as connection:
            table = service.tables['environment_state']
            state = connection.scalar(select(table.c.snapshot))
            state['runtime'] = {'kind': 'unknown', 'inventory': {}}
            connection.execute(update(table).values(snapshot=state))
    elif operation == 'upgrade':
        kwargs['release_selection'] = {'mode': 'exact', 'version': '2.0.0'}
    return actor, declaration, kwargs


def prepared_result(claim):
    operation = claim.inputs.get('change_operation', 'add')
    if operation == 'rollback':
        return result_for(claim.inputs)
    if operation == 'upgrade':
        return upgrade_result(claim)
    value = config_result(claim) if operation == 'config' else result(claim)
    inventory = value['bundle']['inventory']
    for field, before in [('desired_diff', claim.inputs['baseline']['desired_inventory']),
                          ('runtime_diff', claim.inputs['baseline']['runtime'].get('inventory', {}))]:
        value[field] = {name: {'before': before.get(name), 'after': inventory.get(name)}
                       for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
    if claim.inputs['baseline']['runtime']['kind'] != 'normal':
        value['runtime_diff'] = None
    value['warnings'] = Preparation.ready_warnings(claim.inputs)
    return value


@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('marker', MARKERS)
def test_every_capture_replay_readiness_and_approval_refuses_marker_presence(publisher, monkeypatch, operation, marker):
    actor, declaration, kwargs = operation_inputs(publisher, operation)
    first = publisher.prepare(actor, declaration, 'before-save', **kwargs)
    ready_candidate = publisher.prepare(actor, declaration, 'ready-before-save', **kwargs)
    first_claim = publisher.claim(first)
    ready_claim = publisher.claim(ready_candidate)
    assert publisher.publish_ready(ready_claim, prepared_result(ready_claim))
    result_digest = publisher.status(actor, ready_candidate)['result_digest']
    set_marker(publisher, marker)
    before = whole_state(publisher)
    monkeypatch.setattr(publisher, '_capture_approval_authority',
                        lambda *args: pytest.fail('unresolved save reached approval-authority capture'))
    monkeypatch.setattr(publisher, 'project',
                        lambda *args: pytest.fail('unresolved save reached publication projection'))
    for key in ('new-during-save', 'before-save'):
        with pytest.raises(PreparationError, match='configuration save unresolved'):
            publisher.prepare(actor, declaration, key, **kwargs)
        assert whole_state(publisher) == before  # no candidate, dedup, material or intent writes
    with pytest.raises(PreparationError, match='stale candidate'):
        publisher.approve(actor, ready_candidate, result_digest, 'during-save',
                          acknowledge_database_unchanged=operation == 'rollback')
    assert whole_state(publisher) == before
    status = publisher.status(actor, ready_candidate)
    assert status['stale'] and status['preview'] is None and not status['approval_supported']
    assert status['config_drift']['config_save'] == config_save_status(rows(publisher, 'environment_state')[0])
    assert status['config_drift']['config_save']['unresolved'] is True
    assert not publisher.publish_ready(first_claim, prepared_result(first_claim))
    assert publisher.status(actor, first)['state'] == 'failed'
    assert rows(publisher, 'environment_state') == before[0]['environment_state']
    assert rows(publisher, 'revision') == before[0]['revision']
    assert rows(publisher, 'installation_attempt') == before[0]['installation_attempt']


@pytest.mark.parametrize('marker', MARKERS)
@pytest.mark.parametrize('save_id_kind', ['absent', 'foreign', 'matching'])
def test_observation_never_clears_unvalidated_or_unmatched_marker(publisher, marker, save_id_kind):
    publisher.observe_config({'python packages': ['Before<2']}, environ={})
    set_marker(publisher, marker)
    before = whole_state(publisher)
    save_id = None if save_id_kind == 'absent' else str(uuid.uuid4())
    if save_id_kind == 'matching':
        save_id = marker.get('id') if isinstance(marker, dict) else False
    if marker == VALID_MARKER and save_id_kind == 'matching':
        response = publisher.observe_config({'python packages': ['After>=2']}, environ={}, save_id=save_id)
        assert response['generation'] == 2
        assert UNRESOLVED_CONFIG_SAVE not in rows(publisher, 'environment_state')[0]['snapshot']
    else:
        with pytest.raises(PreparationError, match='configuration save unresolved'):
            publisher.observe_config({'python packages': ['After>=2']}, environ={}, save_id=save_id)
        assert whole_state(publisher) == before
    # Only a subsequent valid save can replace a malformed/crashed marker; no timeout deletion.
    replacement = publisher.begin_config_save('2', 'b' * 64)
    publisher.observe_config({'python packages': ['Replacement>=2']}, environ={}, save_id=replacement)
    assert UNRESOLVED_CONFIG_SAVE not in rows(publisher, 'environment_state')[0]['snapshot']
    assert rows(publisher, 'observed_config')[0]['snapshot']['requirements'][0]['declaration'] == 'Replacement>=2'


@pytest.mark.parametrize('marker', [None, False, {}, [], 'SECRET',
    {'id': 'SECRET', 'actor': 'SECRET\n', 'created_at': 'SECRET', 'write_digest': 'SECRET'},
    {**VALID_MARKER, 'created_at': '2026-01-01T00:00:00'},
    {**VALID_MARKER, 'actor': 'x' * 256},
    {**VALID_MARKER, 'write_digest': 'A' * 64},
    {**VALID_MARKER, 'configuration': 'SECRET raw content', 'unknown_field': 'SECRET'}])
def test_safe_marker_status_validates_each_field_without_disclosure(publisher, marker):
    candidate = publisher.prepare('1', 'Example>=2', 'status')
    claim = publisher.claim(candidate)
    assert publisher.publish_ready(claim, prepared_result(claim))
    response = publisher.approve('1', candidate, publisher.status('1', candidate)['result_digest'], 'status')
    before = publisher.status('1', candidate)['config_drift']
    assert before == {'pending': False, 'observed_generation': None, 'committed_generation': None}
    set_marker(publisher, marker)
    expected = config_save_status(rows(publisher, 'environment_state')[0])
    for status in (publisher.status('1', candidate),
                   publisher.publication_status('1', revision_id=response['revision_id'])):
        assert status['config_drift'] == {**before, 'config_save': expected}
        assert 'SECRET' not in repr(status['config_drift'])
    if not isinstance(marker, dict) or not marker or marker.get('id') == 'SECRET':
        assert expected == {'unresolved': True, 'id': None, 'actor': None, 'created_at': None, 'write_digest': None}
    elif all(marker[key] == value for key, value in VALID_MARKER.items()):
        assert expected == {'unresolved': True, **VALID_MARKER}
    else:
        invalid = next(key for key in ('actor', 'created_at', 'write_digest') if marker[key] != VALID_MARKER[key])
        assert expected[invalid] is None
        assert all(expected[key] == value for key, value in VALID_MARKER.items() if key != invalid)


@pytest.mark.parametrize('actor,digest', [('', 'a' * 64), ('x' * 256, 'a' * 64),
    ('2\nSECRET', 'a' * 64), (None, 'a' * 64), ('2', ''), ('2', 'SECRET'),
    ('2', 'A' * 64), ('2', 'a' * 63), ('2', False)])
def test_begin_rejects_invalid_identity_without_sql_change(publisher, actor, digest):
    before = whole_state(publisher)
    with pytest.raises(PreparationError):
        publisher.begin_config_save(actor, digest)
    assert whole_state(publisher) == before


@pytest.mark.parametrize('save_id', [None, '', False, True, 42, 'not-a-uuid', str(uuid.uuid4())])
def test_missing_marker_refuses_supplied_id_but_allows_initial_observation(publisher, save_id):
    if save_id is None:
        assert publisher.observe_config({}, environ={}, save_id=save_id)['generation'] == 1
    else:
        before = whole_state(publisher)
        with pytest.raises(PreparationError, match='marker missing'):
            publisher.observe_config({}, environ={}, save_id=save_id)
        assert whole_state(publisher) == before


@pytest.mark.parametrize('save_id', ['', False, True, 42, {}, []])
def test_valid_marker_never_accepts_malformed_save_id(publisher, save_id):
    set_marker(publisher, VALID_MARKER)
    before = whole_state(publisher)
    with pytest.raises(PreparationError, match='configuration save unresolved'):
        publisher.observe_config({}, environ={}, save_id=save_id)
    assert whole_state(publisher) == before


@pytest.mark.parametrize('field,value', [('id', False), ('id', True), ('id', {}),
    ('actor', None), ('actor', []), ('created_at', False), ('created_at', {}),
    ('write_digest', None), ('write_digest', [])])
def test_nonstring_marker_identity_never_matches_or_raises_in_status(publisher, field, value):
    marker = {**VALID_MARKER, field: value}
    set_marker(publisher, marker)
    status = config_save_status(rows(publisher, 'environment_state')[0])
    assert status == {'unresolved': True, **VALID_MARKER, field: None}
    before = whole_state(publisher)
    with pytest.raises(PreparationError, match='configuration save unresolved'):
        publisher.observe_config({}, environ={}, save_id=marker['id'])
    assert whole_state(publisher) == before


def cloud_writer(write):
    return SimpleNamespace(get_key=lambda name: SimpleNamespace(set_contents_from_string=write))


@pytest.mark.parametrize('failure', ['lock-open', 'cloud', 'local'])
def test_writer_failure_refuses_success_and_preserves_ordering_authority(publisher, monkeypatch, tmp_path, failure):
    filename = tmp_path / 'config.yml'
    filename.write_text('old: value\n')
    cloud_writes = []
    monkeypatch.setattr(adapter, 'internal_publication_service', lambda: publisher)
    if failure == 'lock-open':
        # O_NOFOLLOW must refuse the malicious lock before marker/cloud/local IO.
        Path(str(filename) + '.managed-save.lock').symlink_to(filename)
    elif failure == 'cloud':
        def fail_cloud(content):
            assert UNRESOLVED_CONFIG_SAVE in rows(publisher, 'environment_state')[0]['snapshot']
            raise OSError('SECRET cloud fault')
        cloud_writes = fail_cloud
    else:
        filename.unlink()
        filename.mkdir()  # local open fails after the cloud write and committed marker
    content = 'python packages: [Example>=2]\n'
    before = whole_state(publisher)
    cloud = cloud_writer(cloud_writes if callable(cloud_writes) else cloud_writes.append)
    with pytest.raises(OSError):
        adapter.save_managed_configuration('2', content, {'python packages': ['Example>=2']}, filename, cloud)
    assert rows(publisher, 'observed_config') == []
    if failure == 'lock-open':
        assert whole_state(publisher) == before and cloud_writes == []
        assert filename.read_text() == 'old: value\n'
    else:
        marker = rows(publisher, 'environment_state')[0]['snapshot'][UNRESOLVED_CONFIG_SAVE]
        assert marker['actor'] == '2' and marker['write_digest'] == hashlib.sha256(content.encode()).hexdigest()
        assert str(uuid.UUID(marker['id'])) == marker['id']
        assert datetime.fromisoformat(marker['created_at']).utcoffset() is not None
        with pytest.raises(PreparationError, match='configuration save unresolved'):
            publisher.prepare('2', 'Example>=2', 'after-failure')
        with pytest.raises(PreparationError, match='configuration save unresolved'):
            publisher.observe_config({}, environ={})
        if failure == 'cloud':
            assert filename.read_text() == 'old: value\n'
        else:
            assert cloud_writes == [content]


@pytest.mark.parametrize('failure', ['lock-open', 'cloud'])
@pytest.mark.parametrize('recovery', [False, True])
def test_admin_writer_failure_has_no_restart_or_success(publisher, monkeypatch, tmp_path, failure, recovery):
    client, filename, pipeline_calls, cloud_writes, logs = config_view(
        publisher, monkeypatch, tmp_path, recovery=recovery)
    if failure == 'lock-open':
        Path(str(filename) + '.managed-save.lock').symlink_to(filename)
    else:
        def fail_cloud(content):
            assert UNRESOLVED_CONFIG_SAVE in rows(publisher, 'environment_state')[0]['snapshot']
            raise OSError('SECRET cloud failure')

        # The fixture compiles the actual admin body with a disposable cloud provider.
        client.application.view_functions['config_page'].__globals__['cloud'] = cloud_writer(fail_cloud)
    before = whole_state(publisher)
    response = client.post('/config', data={'config': 'python packages: [Example>=2]\n'})
    assert response.status_code == 503
    assert filename.read_text() == 'old: value\n' and cloud_writes == []
    assert rows(publisher, 'observed_config') == [] and not pipeline_calls
    assert not any(category == 'success' for category, _ in flashes(client))
    assert 'SECRET' not in response.text + repr(flashes(client)) + repr(logs)
    if failure == 'lock-open':
        assert whole_state(publisher) == before
    else:
        after = whole_state(publisher)
        marker = after[0]['environment_state'][0]['snapshot'].pop(UNRESOLVED_CONFIG_SAVE)
        assert marker['actor'] == '1' and marker['id'] and marker['write_digest']
        assert after == before


def test_concurrent_cloud_and_local_saves_cannot_reverse_observation(publisher, monkeypatch, tmp_path):
    filename = tmp_path / 'config.yml'
    filename.write_text('old: value\n')
    first_cloud, release_first, second_lock_attempt = Event(), Event(), Event()
    order, cloud_contents = [], []
    original_observe, original_flock = publisher.observe_config, adapter.fcntl.flock
    monkeypatch.setattr(adapter, 'internal_publication_service', lambda: publisher)

    def flock(fd, operation):
        if first_cloud.is_set():
            second_lock_attempt.set()
        return original_flock(fd, operation)

    def cloud_write(content):
        order.append(('cloud', content))
        cloud_contents.append(content)
        if content == 'python packages: [First]\n':
            first_cloud.set()
            assert release_first.wait(15)

    def observe(configuration, *, save_id):
        content = 'python packages: [' + configuration['python packages'][0] + ']\n'
        assert filename.read_text() == cloud_contents[-1] == content
        marker = rows(publisher, 'environment_state')[0]['snapshot'][UNRESOLVED_CONFIG_SAVE]
        assert marker['id'] == save_id and marker['write_digest'] == hashlib.sha256(content.encode()).hexdigest()
        order.append(('observe', content))
        return original_observe(configuration, environ={}, save_id=save_id)

    monkeypatch.setattr(adapter.fcntl, 'flock', flock)
    monkeypatch.setattr(publisher, 'observe_config', observe)
    cloud = cloud_writer(cloud_write)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(adapter.save_managed_configuration, '2', 'python packages: [First]\n',
                            {'python packages': ['First']}, filename, cloud)
        try:
            assert first_cloud.wait(10)
            first_marker = rows(publisher, 'environment_state')[0]['snapshot'][UNRESOLVED_CONFIG_SAVE]
            second = pool.submit(adapter.save_managed_configuration, '2', 'python packages: [Second]\n',
                                 {'python packages': ['Second']}, filename, cloud)
            assert second_lock_attempt.wait(10)  # explicit attempt at the held flock, not a timing guess
            assert not second.done()
            assert rows(publisher, 'environment_state')[0]['snapshot'][UNRESOLVED_CONFIG_SAVE] == first_marker
            assert filename.read_text() == 'old: value\n' and rows(publisher, 'observed_config') == []
            assert order == [('cloud', 'python packages: [First]\n')]
        finally:
            release_first.set()
        assert first.result(timeout=10)['generation'] == 1
        assert second.result(timeout=10)['generation'] == 2
    assert order == [('cloud', 'python packages: [First]\n'), ('observe', 'python packages: [First]\n'),
                     ('cloud', 'python packages: [Second]\n'), ('observe', 'python packages: [Second]\n')]
    assert filename.read_text() == cloud_contents[-1] == 'python packages: [Second]\n'
    assert rows(publisher, 'observed_config')[0]['snapshot']['requirements'][0]['declaration'] == 'Second'
    assert UNRESOLVED_CONFIG_SAVE not in rows(publisher, 'environment_state')[0]['snapshot']


@pytest.mark.parametrize('death_stage', ['cloud', 'observe'])
def test_writer_death_retains_fence_until_serialized_restart(publisher, monkeypatch, tmp_path, death_stage):
    filename, cloud_file = tmp_path / 'config.yml', tmp_path / 'cloud.yml'
    filename.write_text('old: value\n')
    context = multiprocessing.get_context('fork')
    stopped, release = context.Event(), context.Event()
    monkeypatch.setattr(adapter, 'internal_publication_service', lambda: publisher)
    content = 'python packages: [Interrupted]\n'

    def interrupted_writer():
        # Do not reuse the parent's pooled PostgreSQL connections across a fork.
        publisher.engine.dispose(close=False)
        original = publisher.observe_config

        def cloud_write(value):
            cloud_file.write_text(value)
            if death_stage == 'cloud':
                stopped.set()
                assert release.wait(30)

        def observe(configuration, *, save_id):
            if death_stage == 'observe':
                stopped.set()
                assert release.wait(30)
            return original(configuration, environ={}, save_id=save_id)

        publisher.observe_config = observe
        adapter.save_managed_configuration('2', content, {'python packages': ['Interrupted']},
                                           filename, cloud_writer(cloud_write))

    process = context.Process(target=interrupted_writer)
    process.start()
    try:
        assert stopped.wait(10)
        marker = rows(publisher, 'environment_state')[0]['snapshot'][UNRESOLVED_CONFIG_SAVE]
        assert marker['write_digest'] == hashlib.sha256(content.encode()).hexdigest()
        assert cloud_file.read_text() == content
        assert filename.read_text() == (content if death_stage == 'observe' else 'old: value\n')
        process.kill()
        process.join(timeout=10)
        assert process.exitcode == -9
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=10)
    assert rows(publisher, 'environment_state')[0]['snapshot'][UNRESOLVED_CONFIG_SAVE] == marker
    assert rows(publisher, 'observed_config') == []
    with pytest.raises(PreparationError, match='configuration save unresolved'):
        publisher.observe_config({'python packages': ['Old']}, environ={})
    with pytest.raises(PreparationError, match='configuration save unresolved'):
        publisher.prepare('2', 'Example', 'after-writer-death')
    original = publisher.observe_config
    replacement_ids = []

    def restart_observe(configuration, *, save_id):
        replacement_ids.append(save_id)
        assert save_id != marker['id']
        return original(configuration, environ={}, save_id=save_id)

    monkeypatch.setattr(publisher, 'observe_config', restart_observe)
    restarted = 'python packages: [Restarted]\n'
    response = adapter.save_managed_configuration('2', restarted, {'python packages': ['Restarted']},
        filename, cloud_writer(lambda value: cloud_file.write_text(value)))
    assert replacement_ids and response['generation'] == 1
    assert filename.read_text() == cloud_file.read_text() == restarted
    assert UNRESOLVED_CONFIG_SAVE not in rows(publisher, 'environment_state')[0]['snapshot']
    assert rows(publisher, 'observed_config')[0]['snapshot']['requirements'][0]['declaration'] == 'Restarted'
    assert publisher.prepare('2', 'Example', 'after-restart')
