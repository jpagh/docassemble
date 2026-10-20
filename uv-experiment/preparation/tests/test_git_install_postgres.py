"""10a Git identity through real PostgreSQL preparation/publication (both schemas)."""
# ruff: noqa: F811 -- imported PostgreSQL fixtures are injected by name
from copy import deepcopy
import json
import io
import os
from pathlib import Path
import stat

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from docassemble.base.managed_preparation import PreparationError, digest
from docassemble.base.managed_git import source_declarations, scoped_authorization
from docassemble_executor.git_sources import resolve_sources
from test_preparation_postgres import protocol, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, intent  # noqa: F401
from test_registry_changes_postgres import finish
from test_managed_api_postgres import api_http, call  # noqa: F401
from test_playground_upload_postgres import playground_http  # noqa: F401
from test_zip_install_postgres import store  # noqa: F401

URL = 'https://example.org/repo.git'
DECLARATION = 'Example @ git+' + URL + '@main#subdirectory=python'
CREDENTIAL_DECLARATION = 'Example @ git+https://user:DISPOSABLE_SECRET_TOKEN@example.org/repo.git@main#subdirectory=python'
REDACTED_DECLARATION = 'Example @ git+https://example.org/repo.git@main#subdirectory=python'
ROTATED_DECLARATION = 'Example @ git+https://user:DISPOSABLE_ROTATED_TOKEN@example.org/repo.git@main#subdirectory=python'
SHA = 'a' * 40
NEXT = 'b' * 40


def secret_root(monkeypatch, tmp_path):
    root = tmp_path / 'secrets'
    root.mkdir(mode=0o700)
    monkeypatch.setenv('DA_MANAGED_SECRET_ROOT', str(root))
    return root


def ready_git(publisher, declaration=DECLARATION, key='prepare', *, operation='add', contribution_id=None,
              sha=SHA, kind='branch', actor='1', monkeypatch, secrets=None):
    candidate = publisher.prepare(actor, declaration, key, operation=operation, contribution_id=contribution_id)
    claim = publisher.claim(candidate)
    authorizations = None
    if secrets is not None:
        authorizations = {name: publisher.read_git_credential(claim, secrets, reference)
                          for name, reference in (claim.inputs.get('git_credentials') or {}).items()}
    def resolve(source, directory, **kwargs):
        assert publisher.engine.pool.checkedout() == 0  # network work is never in a DB transaction
        return dict(source, resolved_sha=sha, ref_kind=kind)
    monkeypatch.setattr('docassemble_executor.git_sources.resolve_source', resolve)
    sources = resolve_sources(claim.inputs, Path('/tmp'), authorizations=authorizations)
    inventory = deepcopy(claim.inputs['baseline']['desired_inventory'])
    for name in claim.inputs['baseline'].get('git_sources', {}):
        inventory.pop(name, None)
    for name, source in sources.items():
        inventory[name] = {'version': '1.0', 'source_digest': digest(source)}
    def diff(before):
        return {name: {'before': before.get(name), 'after': inventory.get(name)}
            for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
    result = {'bundle': {'digest': 'd' * 64, 'project_digest': 'e' * 64, 'lock_digest': 'f' * 64,
        'inventory': inventory, 'artifacts': ['b' * 64]}, 'desired_diff': diff(claim.inputs['baseline']['desired_inventory']),
        'runtime_diff': diff(claim.inputs['baseline']['runtime']['inventory']) if claim.inputs['baseline']['runtime']['kind'] == 'normal' else None,
        'warnings': publisher.ready_warnings(claim.inputs), 'ownership_effects': claim.inputs['ownership_effects'],
        'validation': {'core_unchanged': True, 'requirements_satisfied': True, 'durable_materialization_verified': True}}
    if sources or claim.inputs['baseline'].get('git_sources'):
        result['git_sources'] = sources
    if operation == 'config':
        result['config_diff'] = publisher.config_diff(claim.inputs)
    assert publisher.publish_ready(claim, result)
    return candidate, publisher.status(actor, candidate)['result_digest'], claim, result


def approve(publisher, ready, actor='1', key='approve'):
    return publisher.approve(actor, ready[0], ready[1], key)


def current(publisher):
    with publisher.engine.connect() as c:
        return c.scalar(select(publisher.tables['environment_state'].c.snapshot))


def test_git_prepare_no_early_intent_idempotency_and_recorded_sha_publication(publisher, monkeypatch):
    before = snapshot(publisher), intent(publisher)
    ready = ready_git(publisher, monkeypatch=monkeypatch)
    assert (snapshot(publisher), intent(publisher)) == before
    assert publisher.prepare('1', DECLARATION, 'prepare') == ready[0]
    published = approve(publisher, ready)
    state = current(publisher)
    assert state['git_sources']['example']['resolved_sha'] == SHA
    assert state['git_sources']['example']['subdirectory'] == 'python'
    assert state['contributions'][-1]['declaration'] == DECLARATION or any(
        row['declaration'] == DECLARATION for row in state['contributions'])
    assert published['installation'] == 'pending'
    assert intent(publisher)[0][0]['type'] == 'pip'  # managed projection, never legacy Git dispatch


@pytest.mark.parametrize('kind,ref', [('branch', 'main'), ('tag', 'v1'), ('commit', SHA)])
def test_git_unrelated_registry_and_config_solve_preserve_sha(publisher, monkeypatch, kind, ref):
    # The protocol fixture's synthetic config-only identity has no legacy Package;
    # this integration case exercises only identities projected by this publisher.
    with publisher.authority_write() as c:
        c.execute(publisher.tables['requirement_contribution'].delete().where(
            publisher.tables['requirement_contribution'].c.id == 'config-contribution'))
        c.execute(publisher.tables['package_identity'].delete().where(
            publisher.tables['package_identity'].c.id == 'config-package'))
    ready = ready_git(publisher, DECLARATION.replace('@main', '@' + ref), kind=kind, monkeypatch=monkeypatch)
    approve(publisher, ready)
    finish(publisher)
    old = current(publisher)['git_sources']
    next_ready = ready_git(publisher, 'Unrelated', 'unrelated', sha=NEXT, monkeypatch=monkeypatch)
    assert next_ready[3]['git_sources'] == old
    approve(publisher, next_ready, key='approve-unrelated')
    assert current(publisher)['git_sources'] == old
    finish(publisher)
    publisher.observe_config({'python packages': ['example<2']}, environ={})
    imported = ready_git(publisher, None, 'config', operation='config', actor='2', sha=NEXT, monkeypatch=monkeypatch)
    claim = imported[2]
    assert source_declarations(claim.inputs) == {'example': {key: value for key, value in old['example'].items()
        if key not in ('resolved_sha', 'ref_kind')}}
    assert resolve_sources(claim.inputs, Path('/tmp')) == old
    original_intent = intent(publisher)
    approve(publisher, imported, actor='2', key='approve-config')
    assert current(publisher)['git_sources'] == old
    assert intent(publisher) == original_intent


@pytest.mark.parametrize('kind', ['branch', 'tag', 'commit'])
def test_git_targeted_update_advance_branch_fixed_tag_commit(publisher, monkeypatch, kind):
    ref = SHA if kind == 'commit' else 'main'
    approve(publisher, ready_git(publisher, DECLARATION.replace('@main', '@' + ref), kind=kind, monkeypatch=monkeypatch))
    finish(publisher)
    ready = ready_git(publisher, 'example', 'update', operation='update', sha=NEXT if kind == 'branch' else SHA,
                      kind=kind, monkeypatch=monkeypatch)
    assert ready[3]['git_sources']['example']['resolved_sha'] == (NEXT if kind == 'branch' else SHA)
    original = intent(publisher)
    approve(publisher, ready, key='approve-update')
    assert intent(publisher) == original


def test_moved_tag_requires_explicit_owner_replacement_preserves_constraints_history(publisher, monkeypatch):
    first = ready_git(publisher, kind='tag', monkeypatch=monkeypatch)
    published = approve(publisher, first)
    finish(publisher)
    with publisher.authority_write() as c:
        identity = c.scalar(select(publisher.tables['package_identity'].c.id).where(
            publisher.tables['package_identity'].c.normalized_name == 'example'))
        c.execute(publisher.tables['requirement_contribution'].insert().values(id='bound', identity_id=identity,
            origin='config', declaration='example<2', active=True))
    with pytest.raises(PreparationError, match='tag moved; explicit source replacement'):
        ready_git(publisher, 'example', 'update', operation='update', sha=NEXT, kind='tag', monkeypatch=monkeypatch)
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        publisher.prepare('2', DECLARATION, 'not-owner')
    assert (snapshot(publisher), intent(publisher)) == before
    second = ready_git(publisher, key='replacement', sha=NEXT, kind='tag', monkeypatch=monkeypatch)
    assert second[2].inputs['replace_git_contributions']
    assert any(row['declaration'] == 'example<2' for row in second[2].inputs['contributions'])
    approve(publisher, second, key='approve-replacement')
    state = snapshot(publisher)
    old = next(row for row in state['revision'] if row['id'] == published['revision_id'])
    assert old['snapshot']['git_sources']['example']['resolved_sha'] == SHA
    assert current(publisher)['git_sources']['example']['resolved_sha'] == NEXT
    assert len([row for row in state['requirement_contribution'] if row['active'] and '@' in row['declaration']]) == 1


def test_git_remove_last_source_retains_history_not_identity_or_owner_deletion(publisher, monkeypatch):
    first = ready_git(publisher, monkeypatch=monkeypatch)
    published = approve(publisher, first)
    finish(publisher)
    original_grants = intent(publisher)[1]
    contribution = next(row for row in current(publisher)['contributions'] if row['declaration'] == DECLARATION)
    removal = ready_git(publisher, 'example', 'remove', operation='remove', contribution_id=contribution['id'], monkeypatch=monkeypatch)
    assert removal[3]['git_sources'] == {}
    approve(publisher, removal, key='approve-remove')
    assert current(publisher)['git_sources'] == {}
    assert intent(publisher)[1] == original_grants and not intent(publisher)[0][0]['active']
    assert any(row['id'] == published['revision_id'] and row['snapshot']['git_sources'] for row in snapshot(publisher)['revision'])


@pytest.mark.parametrize('revocation', ['role', 'owner'])
def test_git_current_authorization_rechecked_before_publication(publisher, monkeypatch, revocation):
    first = ready_git(publisher, monkeypatch=monkeypatch)
    if revocation == 'owner':
        approve(publisher, first)
        finish(publisher)
        first = ready_git(publisher, key='replace', monkeypatch=monkeypatch)
    with publisher.authority_write() as c:
        c.execute(text('DELETE FROM test_user_roles WHERE user_id=1' if revocation == 'role' else
                       'DELETE FROM test_package_auth WHERE user_id=1'))
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        approve(publisher, first, key='approve-again')
    assert (snapshot(publisher), intent(publisher)) == before


@pytest.mark.parametrize('invalid', ['sha', 'digest', 'subdirectory', 'absent'])
def test_git_ready_result_source_and_inventory_identity_rejected(publisher, monkeypatch, invalid):
    ready = ready_git(publisher, monkeypatch=monkeypatch)
    result = deepcopy(ready[3])
    if invalid == 'sha':
        result['git_sources']['example']['resolved_sha'] = 'main'
    elif invalid == 'digest':
        result['bundle']['inventory']['example']['source_digest'] = 'f' * 64
    elif invalid == 'absent':
        result['git_sources'] = {}
    else:
        result['git_sources']['example']['subdirectory'] = 'elsewhere'
    with pytest.raises(PreparationError):
        publisher._validate_result(ready[2].inputs, result)


@pytest.mark.parametrize('transport', ['named', 'legacy'])
def test_git_http_prepare_routes_without_legacy_install_or_intent(api_http, transport):
    h = api_http
    before = snapshot(h.service), intent(h.service)
    body = {'pip': DECLARATION} if transport == 'named' else {
        'github_url': URL + '#subdirectory=python', 'branch': 'main', 'requirement': 'Example'}
    response = call(h, '/api/package', body={**body, 'idempotency_key': 'git'})
    assert response.status_code == 202, response.json
    assert (snapshot(h.service), intent(h.service)) == before
    claim = h.service.claim(response.json['candidate_id'])
    assert claim.inputs['git_source'] == {'repository': URL, 'subdirectory': 'python', 'declared_ref': 'main'}


@pytest.mark.parametrize('failure', ['userinfo', 'query', 'unnamed', 'readonly', 'restart'])
def test_git_http_rejection_redaction_authority_and_no_candidate(api_http, failure):
    h = api_http
    body = {'github_url': URL, 'requirement': 'Example', 'idempotency_key': 'git'}
    if failure == 'userinfo':
        body['github_url'] = 'https://SECRET@example.org/repo.git'
    elif failure == 'query':
        body['github_url'] += '?token=SECRET'
    elif failure == 'unnamed':
        body.pop('requirement')
    elif failure == 'restart':
        body['restart'] = False
    else:
        h.info['permissions'] = ['read_packages']
        h.redis.set(h.redis_key, json.dumps(h.info))
    before = snapshot(h.service), intent(h.service)
    response = call(h, '/api/package', body=body)
    assert response.status_code == (403 if failure == 'readonly' else 409), response.json
    assert 'SECRET' not in response.get_data(as_text=True)
    assert (snapshot(h.service), intent(h.service)) == before
    with h.service.engine.connect() as c:
        assert c.scalar(select(h.service.tables['candidate'].c.id)) is None


@pytest.mark.parametrize('empty_upload', [False, True])
def test_git_browser_named_form_prepares_without_legacy_intent(playground_http, empty_upload):
    h = playground_http
    before = snapshot(h.service), intent(h.service)
    data = {'giturl': URL, 'gitbranch': 'main', 'requirement': 'Example', 'pippackage': '',
            'csrf_token': 'framework-fixture', 'submit': 'Install'}
    if empty_upload:
        data['zipfile'] = (io.BytesIO(b''), '')
    reply = h.client.post('/updatepackage', data=data)
    assert reply.status_code == 202, reply.json
    assert (snapshot(h.service), intent(h.service)) == before
    assert not h.legacy_calls
    with h.client.session_transaction() as session:
        assert 'taskwait' not in session and 'serverstarttime' not in session
    with h.service.engine.connect() as c:
        candidate = c.execute(select(h.service.tables['candidate'])).mappings().one()
    assert candidate['inputs']['git_source'] == {'repository': URL, 'declared_ref': 'main', 'subdirectory': None}


def test_git_browser_invalid_form_stops_before_prepare(playground_http, monkeypatch):
    from types import SimpleNamespace
    h = playground_http
    monkeypatch.setitem(h.playground_namespace, 'UpdatePackageForm', lambda form: SimpleNamespace(
        gitbranch=SimpleNamespace(choices=[]), validate_on_submit=lambda: False))
    reply = h.client.post('/updatepackage', data={'giturl': URL, 'requirement': 'Example'})
    assert reply.status_code == 409 and reply.json['code'] == 'rejected'
    with h.service.engine.connect() as c:
        assert c.scalar(select(h.service.tables['candidate'].c.id)) is None


def test_git_credential_redacted_staged_scoped_and_promoted(publisher, monkeypatch, tmp_path):
    secrets = secret_root(monkeypatch, tmp_path)
    ready = ready_git(publisher, CREDENTIAL_DECLARATION, 'credential', monkeypatch=monkeypatch, secrets=secrets)
    claim = ready[2]
    reference = claim.inputs['git_credentials']['example']
    assert 'DISPOSABLE_SECRET_TOKEN' not in json.dumps(claim.inputs)
    assert claim.inputs['requirement']['declaration'] == REDACTED_DECLARATION
    assert claim.inputs['git_source'] == {'repository': URL, 'subdirectory': 'python', 'declared_ref': 'main'}
    payload = (secrets / reference).read_bytes()
    assert b'DISPOSABLE_SECRET_TOKEN' not in payload
    assert scoped_authorization(URL, payload).startswith('Basic ')
    with publisher.engine.connect() as c:
        material = c.execute(select(publisher.tables['candidate_material'])).mappings().one()
    assert material['id'] == reference and not material['committed'] and material['usable']
    published = approve(publisher, ready)
    with publisher.engine.connect() as c:
        material = c.execute(select(publisher.tables['candidate_material'])).mappings().one()
    assert material['committed']
    state = current(publisher)
    assert state['git_credentials'] == {'example': reference}
    assert state['git_sources']['example']['repository'] == URL
    revision = next(row for row in snapshot(publisher)['revision'] if row['id'] == published['revision_id'])
    assert 'DISPOSABLE_SECRET_TOKEN' not in json.dumps(revision)
    assert (secrets / reference).stat().st_mode & 0o777 == 0o600


def test_git_committed_credential_reused_for_unrelated_prepare(publisher, monkeypatch, tmp_path):
    secrets = secret_root(monkeypatch, tmp_path)
    first = ready_git(publisher, CREDENTIAL_DECLARATION, 'credential', monkeypatch=monkeypatch, secrets=secrets)
    reference = first[2].inputs['git_credentials']['example']
    approve(publisher, first)
    finish(publisher)
    unrelated = ready_git(publisher, 'Unrelated', 'unrelated', monkeypatch=monkeypatch, secrets=secrets)
    assert unrelated[2].inputs['git_credentials'] == {'example': reference}
    assert unrelated[3]['git_sources'] == first[3]['git_sources']


def test_git_repository_replacement_drops_an_out_of_scope_reference(publisher, monkeypatch, tmp_path):
    secrets = secret_root(monkeypatch, tmp_path)
    first = ready_git(publisher, CREDENTIAL_DECLARATION, 'credential', monkeypatch=monkeypatch, secrets=secrets)
    approve(publisher, first)
    finish(publisher)
    other = 'Example @ git+https://example.org/other.git@main#subdirectory=python'
    replacement = publisher.prepare('1', other, 'other-repo')
    claim = publisher.claim(replacement)
    assert 'git_credentials' not in claim.inputs
    assert claim.inputs['git_source']['repository'] == 'https://example.org/other.git'


def test_git_rotation_supersedes_current_reference_and_keeps_history(publisher, monkeypatch, tmp_path):
    secrets = secret_root(monkeypatch, tmp_path)
    first = ready_git(publisher, CREDENTIAL_DECLARATION, 'credential', monkeypatch=monkeypatch, secrets=secrets)
    old_reference = first[2].inputs['git_credentials']['example']
    published = approve(publisher, first)
    finish(publisher)
    rotated = ready_git(publisher, ROTATED_DECLARATION, 'rotate', monkeypatch=monkeypatch, secrets=secrets)
    new_reference = rotated[2].inputs['git_credentials']['example']
    assert new_reference != old_reference
    approve(publisher, rotated, key='approve-rotate')
    assert current(publisher)['git_credentials'] == {'example': new_reference}
    with publisher.engine.connect() as c:
        committed = {row['id']: row['committed']
                     for row in c.execute(select(publisher.tables['candidate_material'])).mappings()}
    assert committed[old_reference] and committed[new_reference]
    old = next(row for row in snapshot(publisher)['revision'] if row['id'] == published['revision_id'])
    assert old['snapshot']['git_credentials'] == {'example': old_reference}


def test_git_candidate_credential_cleanup_after_cancel_and_worker_exit(publisher, monkeypatch, tmp_path):
    secrets = secret_root(monkeypatch, tmp_path)
    candidate = publisher.prepare('1', CREDENTIAL_DECLARATION, 'cleanup')
    claim = publisher.claim(candidate)
    reference = claim.inputs['git_credentials']['example']
    assert (secrets / reference).exists()
    publisher.cancel('1', candidate)
    publisher.worker_exited(claim.worker_id)
    assert publisher.delete_candidate_material(candidate, secrets) == 1
    assert not (secrets / reference).exists()


def test_git_credential_worker_failure_and_cleanup_before_staging(publisher, monkeypatch, tmp_path):
    from docassemble_executor.preparation_poller import PreparationPoller
    secrets = secret_root(monkeypatch, tmp_path)
    stage = publisher._stage_queued_secret
    observed = {}

    def worker_before_write(candidate, root, reference, secret):
        # Registration has committed, but the file is not there yet. The real
        # dispatcher claims it and fails closed before calling the builder.
        assert not (root / reference).exists()
        assert PreparationPoller(publisher, object(), material_root=root).run_once() == 'failed'
        with publisher.engine.connect() as c:
            worker = c.execute(select(publisher.tables['preparation_worker'])).mappings().one()
        # The real subreaper receipt, reconciled by the poller, is exit authority;
        # no manual worker-exit report may stand in for descendant exclusion.
        assert worker['exited'] is True
        directory = root / '.direct-workers' / worker['id']
        assert json.loads((directory / 'claim.json').read_text()) == {'worker': worker['id']}
        assert json.loads((directory / 'exit.json').read_text()) == {'worker': worker['id']}
        # 10.2: the descendant-aware worker-failure path performs the cleanup, so the
        # independent reconcile pass has nothing left to remove (still idempotent).
        assert publisher.reconcile_material(root) == 0
        assert not (root / reference).exists()
        observed.update(candidate=candidate, reference=reference, secret=secret)
        stage(candidate, root, reference, secret)

    monkeypatch.setattr(publisher, '_stage_queued_secret', worker_before_write)
    with pytest.raises(PreparationError, match='staging no longer authorized'):
        publisher.prepare('1', CREDENTIAL_DECLARATION, 'interleaved')
    with publisher.engine.connect() as c:
        row = c.execute(select(publisher.tables['candidate_material'])).mappings().one()
        assert row['id'] == observed['reference'] and row['deleted'] and not row['usable']
    # The descendant-aware receipt store is private control evidence, not material.
    receipts = secrets / '.direct-workers'
    assert receipts.is_dir() and not receipts.is_symlink()
    assert stat.S_IMODE(receipts.stat().st_mode) == 0o700
    assert receipts.stat().st_uid == os.getuid()
    assert [entry.name for entry in secrets.iterdir() if entry.name != '.direct-workers'] == []
    for path in receipts.rglob('*'):
        assert not path.is_symlink()
        assert path.name not in ('result.json', 'result.pending', observed['reference'])
        if path.is_dir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o700
            assert path.stat().st_uid == os.getuid()
        else:
            assert path.is_file()
            contents = path.read_bytes()
            assert b'DISPOSABLE_SECRET_TOKEN' not in contents
            assert observed['secret'] not in contents
            assert scoped_authorization(URL, observed['secret']).encode() not in contents
    assert publisher.reconcile_material(secrets) == 0


@pytest.mark.parametrize('interleaving', ['claim', 'cancel', 'invalidated', 'deleted', 'missing',
                                         'shared', 'committed', 'path'])
def test_git_credential_staging_rechecks_candidate_and_material(publisher, monkeypatch, tmp_path, interleaving):
    secrets = secret_root(monkeypatch, tmp_path)
    stage = publisher._stage_queued_secret

    def change_before_write(candidate, root, reference, secret):
        if interleaving == 'claim':
            publisher.claim(candidate)
        elif interleaving == 'cancel':
            publisher.cancel('1', candidate)
        else:
            material = publisher.tables['candidate_material']
            with publisher.engine.begin() as c:
                publisher._environment(c)
                if interleaving == 'missing':
                    c.execute(material.delete().where(material.c.id == reference))
                else:
                    changes = {'invalidated': {'usable': False}, 'deleted': {'deleted': True},
                               'shared': {'shared': True}, 'committed': {'committed': True},
                               'path': {'path': str(root / 'wrong')}}
                    c.execute(update(material).where(material.c.id == reference).values(**changes[interleaving]))
        stage(candidate, root, reference, secret)

    monkeypatch.setattr(publisher, '_stage_queued_secret', change_before_write)
    with pytest.raises(PreparationError, match='staging no longer authorized'):
        publisher.prepare('1', CREDENTIAL_DECLARATION, 'interleaved')
    assert list(secrets.iterdir()) == []


def test_git_credential_staging_holds_environment_lock(publisher, monkeypatch, tmp_path):
    secrets = secret_root(monkeypatch, tmp_path)
    write = publisher._stage_secret_file

    def check_lock_then_write(root, reference, secret):
        # Both claim and cleanup take this row lock first. Neither may pass the
        # eligibility check while the writer can still create secret bytes.
        with pytest.raises(DBAPIError) as error:
            with publisher.engine.begin() as c:
                c.execute(select(publisher.tables['environment_state']).with_for_update(nowait=True))
        assert error.value.orig.pgcode == '55P03'
        with publisher.engine.connect() as c:
            material = c.execute(select(publisher.tables['candidate_material'])).mappings().one()
        assert material['id'] == reference and not material['deleted']  # Already durable.
        write(root, reference, secret)

    monkeypatch.setattr(publisher, '_stage_secret_file', check_lock_then_write)
    candidate = publisher.prepare('1', CREDENTIAL_DECLARATION, 'locked-write')
    claim = publisher.claim(candidate)
    assert publisher.read_git_credential(claim, secrets, claim.inputs['git_credentials']['example'])


@pytest.mark.parametrize('after_write', [False, True])
def test_git_credential_staging_crash_keeps_durable_cleanup_reference(publisher, monkeypatch, tmp_path, after_write):
    secrets = secret_root(monkeypatch, tmp_path)
    write = publisher._stage_secret_file

    def crash(root, reference, secret):
        if after_write:
            write(root, reference, secret)
        raise RuntimeError('simulated staging crash')

    monkeypatch.setattr(publisher, '_stage_secret_file', crash)
    with pytest.raises(RuntimeError, match='simulated staging crash'):
        publisher.prepare('1', CREDENTIAL_DECLARATION, 'crash')
    with publisher.engine.connect() as c:
        material = c.execute(select(publisher.tables['candidate_material'])).mappings().one()
    assert (secrets / material['id']).exists() == after_write
    publisher.cancel('1', material['candidate_id'])
    assert publisher.reconcile_material(secrets) == 1
    assert list(secrets.iterdir()) == []
    assert publisher.reconcile_material(secrets) == 0


def test_git_credential_configuration_required_fails_closed(publisher, monkeypatch, tmp_path):
    monkeypatch.delenv('DA_MANAGED_SECRET_ROOT', raising=False)
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='candidate secret store configuration'):
        publisher.prepare('1', CREDENTIAL_DECLARATION, 'unconfigured')
    assert snapshot(publisher) == before
    with publisher.engine.connect() as c:
        assert c.scalar(select(publisher.tables['candidate'].c.id)) is None


def test_git_revocation_fails_closed_actionably_without_echoing_the_secret(publisher, monkeypatch, tmp_path):
    from docassemble_executor.preparation_poller import PreparationPoller
    secrets = secret_root(monkeypatch, tmp_path)
    first = ready_git(publisher, CREDENTIAL_DECLARATION, 'credential', monkeypatch=monkeypatch, secrets=secrets)
    reference = first[2].inputs['git_credentials']['example']
    approve(publisher, first)
    finish(publisher)
    (secrets / reference).unlink()  # Operator revocation of retained restricted material.
    candidate = publisher.prepare('1', 'Unrelated', 'revoked')
    claim = publisher.claim(candidate)
    with pytest.raises(PreparationError) as error:
        publisher.read_git_credential(claim, secrets, claim.inputs['git_credentials']['example'])
    assert str(error.value) == 'Git credential reference unavailable; reauthorize or provide a new source'
    assert 'DISPOSABLE_SECRET_TOKEN' not in str(error.value)
    revoked = publisher.prepare('1', 'Unrelated', 'revoked-again')
    assert PreparationPoller(publisher, object(), material_root=secrets).run_once() == 'failed'
    with publisher.engine.connect() as c:
        state = c.execute(select(publisher.tables['candidate'].c.state).where(
            publisher.tables['candidate'].c.id == revoked)).scalar()
    assert state == 'failed'
    # Unrelated public sources stay usable; revocation never rewrites history.
    assert publisher.claim(publisher.prepare('1', 'Stillfine>=1', 'unaffected'))
    assert current(publisher)['git_credentials'] == {'example': reference}


def test_git_http_credential_transport_redacted_and_named(api_http, monkeypatch, tmp_path):
    h = api_http
    secret_root(monkeypatch, tmp_path)
    body = {'github_url': 'https://user:DISPOSABLE_SECRET_TOKEN@example.org/repo.git#subdirectory=python',
            'branch': 'main', 'requirement': 'Example', 'idempotency_key': 'git-credential'}
    response = call(h, '/api/package', body=body)
    assert response.status_code == 202, response.json
    assert 'DISPOSABLE_SECRET_TOKEN' not in response.get_data(as_text=True)
    claim = h.service.claim(response.json['candidate_id'])
    assert 'DISPOSABLE_SECRET_TOKEN' not in json.dumps(claim.inputs)
    assert claim.inputs['git_source'] == {'repository': URL, 'subdirectory': 'python', 'declared_ref': 'main'}
    assert claim.inputs['requirement']['declaration'] == REDACTED_DECLARATION
    assert claim.inputs['git_credentials']['example']


def test_git_api_transport_never_infers_the_name_from_the_url(api_http):
    h = api_http
    before = snapshot(h.service), intent(h.service)
    response = call(h, '/api/package', body={'github_url': URL, 'idempotency_key': 'unnamed'})
    assert response.status_code == 409 and response.json['code'] == 'rejected'
    assert 'explicit package name' in response.get_data(as_text=True)
    assert (snapshot(h.service), intent(h.service)) == before
    with h.service.engine.connect() as c:
        assert c.scalar(select(h.service.tables['candidate'].c.id)) is None


def test_git_browser_transport_never_infers_the_name_from_the_url(playground_http):
    h = playground_http
    before = snapshot(h.service), intent(h.service)
    reply = h.client.post('/updatepackage', data={'giturl': URL, 'gitbranch': 'main', 'requirement': '',
        'csrf_token': 'framework-fixture', 'submit': 'Install'})
    assert reply.status_code == 409 and reply.json['code'] == 'rejected'
    assert 'explicit package name' in reply.get_data(as_text=True)
    assert (snapshot(h.service), intent(h.service)) == before
    assert not h.legacy_calls
    with h.service.engine.connect() as c:
        assert c.scalar(select(h.service.tables['candidate'].c.id)) is None


def test_browser_git_form_declares_the_explicit_name_field():
    root = Path(__file__).resolve().parents[3]
    forms = (root / 'docassemble_webapp/docassemble/webapp/packages/forms.py').read_text()
    template = (root / 'docassemble_webapp/docassemble/webapp/packages/templates/packages/update_package.html').read_text()
    script = (root / 'docassemble_webapp/docassemble/webapp/static/app/update_package.js').read_text()
    assert 'requirement = StringField' in forms
    assert 'render_field(form.requirement' in template
    assert '#requirement' in script
