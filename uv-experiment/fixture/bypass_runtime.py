"""Disposable proof of real public legacy calls, run as admitted www-data work."""
import argparse
import os
import re
from pathlib import Path
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True, choices=['installer', 'installers', 'intent', 'control', 'tasks', 'api', 'all'])
    parser.add_argument('--barrier')
    parser.add_argument('--flag-off', action='store_true')
    args = parser.parse_args()
    import docassemble.base.config
    docassemble.base.config.load(arguments=[os.environ['DA_CONFIG_FILE']])
    from docassemble.base.managed import ManagedOperationError
    from docassemble.webapp import update
    from docassemble.webapp.utils import helpers
    from docassemble.webapp.main import helpers as control
    from docassemble.webapp import restart
    from docassemble.webapp.tasks import tasks
    operations = {
        'installer': [('install', lambda: update.install_package(None))],
        'tasks': [
            ('update task', tasks.update_packages.run),
            ('no-restart update task', lambda: tasks.update_packages.run(restart=False)),
            ('reset task', lambda: tasks.reset_server.run(None)),
        ],
        'control': [
            ('sync', helpers.call_sync),
            ('update dispatch', control.trigger_update),
            ('remote restart', lambda: control.restart_on(None)),
            ('all restart', control.restart_all),
            ('local restart', control.restart_this),
            ('other restart', control.restart_others),
            ('restart main', restart.main),
        ],
        'intent': [
            ('pip intent', lambda: helpers.install_pip_package('docassemble.refused', '>=1')),
            ('git intent', lambda: helpers.install_git_package('docassemble.refused', 'https://example.com/refused', 'main')),
            ('zip intent', lambda: helpers.install_zip_package('docassemble.refused', 1)),
            ('remove intent', lambda: helpers.uninstall_package('docassemble.refused')),
        ],
        'installers': [
            ('uninstall', lambda: update.uninstall_package(None)),
            ('reconcile', update.check_for_updates),
            ('versions', lambda: update.update_versions(None)),
            ('dependencies', lambda: update.add_dependencies(None, None)),
            ('names', update.fix_names),
            ('package cleanup', update.clear_invalid_package),
            ('auth cleanup', update.clear_invalid_package_auth),
            ('update main', update.main),
        ],
    }
    if args.barrier:
        ready, release = Path(args.barrier + '.ready'), Path(args.barrier + '.release')
        ready.touch()
        while not release.exists():
            time.sleep(0.05)
    if args.flag_off:
        os.environ['DA_UV_MANAGED'] = 'false'  # Existing imports/queued code do not get a flag-off exception.
    selected = [item for group in operations.values() for item in group] if args.case == 'all' else operations.get(args.case, [])
    for name, operation in selected:
        try:
            operation()
        except ManagedOperationError as error:
            print(str(error))
        else:
            raise AssertionError('legacy operation was not rejected: ' + name)
    if args.case in ('all', 'api'):
        prove_api()
    print('PROOF_OK uid=' + str(os.geteuid()) + ' flag=' + os.environ['DA_UV_MANAGED'])


def prove_api():
    from docassemble.webapp.server import application
    from sqlalchemy import select
    from docassemble.webapp.api.helpers import add_api_key
    from docassemble.webapp.extensions import db
    from docassemble.webapp.packages.managed import preview_service
    from docassemble.webapp.packages.models import Package, PackageAuth
    from docassemble.webapp.users.models import UserModel
    # Exercise the handlers even if an already-running app has stale UI flags.
    application.config.update(ALLOW_UPDATES=True, ENABLE_PLAYGROUND=True, ALLOW_RESTARTING=True)
    from docassemble.webapp.develop import develop_bp
    import docassemble.webapp.develop.views  # noqa: F401
    application.register_blueprint(develop_bp)
    with application.test_client() as client:
        page = client.get('/user/sign-in')
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)
        login = client.post('/user/sign-in', data={'csrf_token': token, 'email': 'admin@example.com',
                                                 'password': os.environ.get('DA_ADMIN_PASSWORD', 'FixtureAdmin101!'), 'submit': 'Sign in'})
        assert login.status_code == 302, login.text
        for path in ('/logs', '/logs?file=docassemble.log'):
            response = client.get(path)
            assert response.status_code == 200, response.text
        page = client.post('/manage_api', data={'csrf_token': token, 'action': 'new', 'name': 'Bypass proof',
                                               'method': 'none', 'security': '[]', 'submit': 'Create'})
        key = re.search(r'id="daApiKey">([^<]+)<', page.text).group(1)
        headers = {'X-API-Key': key}
        with application.app_context():
            actor = db.session.scalar(select(UserModel.id).where(UserModel.email == 'admin@example.com'))
            read_key = add_api_key(actor, 'Bypass proof read-only', 'none', [], permissions=['read_packages'])
            service = preview_service()
        assert read_key
        read_headers = {'X-API-Key': read_key}
        sentinel = 'bypass-secret-token SQL Traceback /var/lib/docassemble/managed/private'

        def refused(response, code):
            # Ticket 07: distinguishable fixed, redacted {error, code} refusals;
            # neither request/provider text nor credentials may cross this boundary.
            assert response.status_code == (403 if code == 'forbidden' else 409), response.text
            assert set(response.json) == {'error', 'code'}, response.text
            assert response.json['code'] == code, response.text
            assert isinstance(response.json['error'], str) and response.json['error'], response.text
            for private in (key, read_key, sentinel, 'bypass-secret-token', 'SQL', 'Traceback',
                            '/var/lib/docassemble/managed/private'):
                assert private not in response.text
            if code == 'forbidden':
                assert response.json['error'] == 'forbidden', response.text
            if code == 'restart-required':
                # Ticket 07: refusal must explain the explicit prepare/approve alternative.
                assert 'POST /api/package_preview' in response.json['error'], response.text
                assert 'separate explicit approval' in response.json['error'], response.text

        def intent_snapshot():
            # Ticket-07 acceptance: candidate preparation/cancellation changes neither
            # committed SQL intent/runtime/publication nor the legacy package projection.
            tables = [service.tables[name] for name in (
                'environment_state', 'revision', 'package_identity', 'requirement_contribution',
                'installation_attempt', 'publication_result', 'retry_result')]
            tables += [Package.__table__, PackageAuth.__table__]
            with service.engine.connect() as connection:
                return {table.name: [dict(row) for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns)).mappings()] for table in tables}

        before = intent_snapshot()
        uv_log = Path('/var/log/docassemble-uv-invocations.log')
        uv_before = uv_log.read_bytes()
        # Ticket 07: API-key identity is mandatory even with an authenticated browser session.
        refused(client.get('/api/package'), 'forbidden')
        # Ticket 07 replaces inventory GET with authorized candidate status. No id
        # is a fixed 409 refusal, not a successful legacy inventory response.
        missing = client.get('/api/package', headers=headers)
        refused(missing, 'rejected')
        assert missing.json == {'code': 'rejected', 'error': 'candidate id required'}, missing.text
        for candidate_id in (sentinel, sentinel + '-different'):
            response = client.get('/api/package', query_string={'candidate_id': candidate_id}, headers=headers)
            refused(response, 'rejected')
            assert response.json == {'code': 'rejected', 'error': 'invalid candidate id'}, response.text
        for restart in ('true', 'false'):
            for data in ({'pip': 'docassemble.refused'}, {'update': 'docassemble.webapp'},
                         {'github_url': 'https://example.com/refused.git'}):
                response = client.post('/api/package', json=dict(data, restart=restart), headers=headers)
                assert response.status_code == 409, response.text
                # Ticket 07: JSON restart must be boolean true; BOTH legacy string
                # values here use restart-required, not a raw 'managed' substring.
                # Git's missing explicit name is rejected during shape translation,
                # before the restart policy (the shim never infers a package name).
                refused(response, 'rejected' if 'github_url' in data else 'restart-required')
            response = client.delete('/api/package?package=docassemble.refused&restart=' + restart, headers=headers)
            assert response.status_code == 409, response.text
            refused(response, 'restart-required' if restart == 'false' else 'rejected')
        assert intent_snapshot() == before
        assert uv_log.read_bytes() == uv_before

        prepare = {'pip': 'docassemble.refused', 'idempotency_key': 'bypass-proof-' + str(uuid.uuid4())}
        # Ticket 07: a read-only token cannot mutate, even for the same admin.
        refused(client.post('/api/package', json=prepare, headers=read_headers), 'forbidden')
        # Ticket 07: POST is prepare-only (202 + durable candidate identity), never
        # legacy install/dispatch/approval. No solve or publication is requested here.
        prepared = client.post('/api/package', json=prepare, headers=headers)
        assert prepared.status_code == 202, prepared.text
        candidate = prepared.json['candidate_id']
        assert candidate == prepared.json['id'] and str(uuid.UUID(candidate)) == candidate, prepared.text
        assert prepared.json['state'] == 'queued', prepared.text
        assert prepared.json['installation_requested'] is False, prepared.text
        status_path = '/api/package?candidate_id=' + candidate
        # Ticket 07: a valid candidate id returns authorized status (200), not inventory.
        for credential in (headers, read_headers):
            response = client.get(status_path, headers=credential)
            assert response.status_code == 200, response.text
            assert response.json['candidate_id'] == candidate, response.text
            assert response.json['state'] == 'queued', response.text
            assert response.json['installation_requested'] is False, response.text
        refused(client.get(status_path), 'forbidden')
        refused(client.post('/api/package_preview', json={'action': 'cancel', 'candidate_id': candidate},
                            headers=read_headers), 'forbidden')
        assert intent_snapshot() == before
        assert uv_log.read_bytes() == uv_before
        for path in ('/updatepackage?action=update&package=docassemble.webapp',
                     '/createplaygroundpackage?install=1&package=refused'):
            response = client.get(path)
            assert response.status_code == 409, response.text
        response = client.post('/restart_ajax', data={'csrf_token': token, 'action': 'restart'})
        assert response.status_code == 409, response.text
        # Ticket-07 acceptance: cancel the unapproved candidate; intent/runtime and
        # uv evidence remain unchanged across all refused legacy and managed calls.
        response = client.post('/api/package_preview', json={'action': 'cancel', 'candidate_id': candidate}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json['candidate_id'] == candidate and response.json['state'] == 'cancelled', response.text
        assert response.json['installation_requested'] is False, response.text
        after = client.get('/api/package', headers=headers)
        refused(after, 'rejected')
        assert after.json == missing.json, after.text
        assert intent_snapshot() == before
        assert uv_log.read_bytes() == uv_before
        print('actual API/UI legacy refusals and managed prepare/status/auth/cancel proved; SQL intent/runtime and uv log unchanged')


if __name__ == '__main__':
    main()
