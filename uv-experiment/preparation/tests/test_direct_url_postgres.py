"""Direct wheel/archive URLs through real PostgreSQL preparation and publication."""
# ruff: noqa: F811 -- imported PostgreSQL fixtures are injected by name
from copy import deepcopy
from contextlib import contextmanager
import io
import os
from select import select as select_fds
import signal
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import uuid
import zipfile

import pytest
from sqlalchemy import select

from docassemble.base.managed_preparation import PreparationError
from docassemble_executor.direct_sources import resolve_direct_sources
from test_preparation_postgres import protocol, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, intent  # noqa: F401
from test_managed_api_postgres import api_http  # noqa: F401
from test_registry_changes_postgres import finish

URL = 'https://example.org/files/zip_example-1.0-py3-none-any.whl'
DECLARATION = 'ZipExample @ ' + URL
CREDENTIAL = 'ZipExample @ https://user:DISPOSABLE_SECRET_TOKEN@example.org/files/zip_example-1.0-py3-none-any.whl?token=DISPOSABLE_SECRET_TOKEN'


def wheel_bytes(name='ZipExample', version='1.0'):
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, 'w') as archive:
            dist = name.lower().replace('-', '_') + '-' + version
            archive.writestr(dist + '.dist-info/METADATA', 'Name: ' + name + '\nVersion: ' + version + '\n')
            archive.writestr(dist + '.dist-info/WHEEL', 'Wheel-Version: 1.0\nTag: py3-none-any\n')
        return buffer.getvalue()


@pytest.fixture
def store(tmp_path):
    root = tmp_path / 'artifacts'
    root.mkdir(mode=0o700)
    return root


def ready_direct(publisher, store, declaration, key='prepare', *, operation='add', contribution_id=None,
                 actor='1', download=None):
    candidate = publisher.prepare(actor, declaration, key, operation=operation, contribution_id=contribution_id)
    claim = publisher.claim(candidate)
    sources, _ = resolve_direct_sources(claim.inputs, store, download=download)
    for source in sources.values():
        publisher.register_material(claim, str(uuid.uuid4()), str(store / source['digest']))
    result = direct_result(publisher, claim.inputs, sources)
    assert publisher.publish_ready(claim, result)
    return candidate, publisher.status(actor, candidate)['result_digest'], claim, sources


def direct_result(publisher, inputs, sources):
    inventory = deepcopy(inputs['baseline']['desired_inventory'])
    for name in inputs['baseline'].get('direct_sources', {}):
        inventory.pop(name, None)
    for name, source in sources.items():
        inventory[name] = {'version': source['version'], 'source_digest': source['digest']}
    def diff(before):
        return {name: {'before': before.get(name), 'after': inventory.get(name)}
            for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
    result = {'bundle': {'digest': 'd' * 64, 'project_digest': 'e' * 64, 'lock_digest': 'f' * 64,
        'inventory': inventory, 'artifacts': ['b' * 64] + [source['digest'] for source in sources.values()]},
        'desired_diff': diff(inputs['baseline']['desired_inventory']),
        'runtime_diff': diff(inputs['baseline']['runtime']['inventory']) if inputs['baseline']['runtime']['kind'] == 'normal' else None,
        'warnings': publisher.ready_warnings(inputs), 'ownership_effects': inputs['ownership_effects'],
        'validation': {'core_unchanged': True, 'requirements_satisfied': True, 'durable_materialization_verified': True}}
    if sources or inputs['baseline'].get('direct_sources'):
        result['direct_sources'] = sources
    return result


def approve(publisher, ready, actor='1', key='approve'):
    return publisher.approve(actor, ready[0], ready[1], key)


def current(publisher):
    with publisher.engine.connect() as c:
        return c.scalar(select(publisher.tables['environment_state'].c.snapshot))


def test_direct_prepare_no_early_intent_records_raw_identity_and_redacts_declaration(publisher, store):
    before = snapshot(publisher), intent(publisher)
    candidate = publisher.prepare('1', CREDENTIAL, 'prepare')
    assert (snapshot(publisher), intent(publisher)) == before
    claim = publisher.claim(candidate)
    assert claim.inputs['direct_source']['url'] == (
        'https://user:DISPOSABLE_SECRET_TOKEN@example.org/files/zip_example-1.0-py3-none-any.whl?token=DISPOSABLE_SECRET_TOKEN')
    assert claim.inputs['requirement']['declaration'] == (
        'ZipExample @ https://example.org/files/zip_example-1.0-py3-none-any.whl?REDACTED')
    assert claim.inputs['replace_direct_contributions'] == []
    assert 'DISPOSABLE_SECRET_TOKEN' not in claim.inputs['requirement']['declaration']
    assert publisher.status('1', candidate)['intent_change']['requirement'] == claim.inputs['requirement']['declaration']


def test_direct_publish_records_sources_promotes_bytes_and_has_no_legacy_intent(publisher, store):
    ready = ready_direct(publisher, store, DECLARATION, download=lambda url: wheel_bytes())
    source = ready[3]['zipexample']
    published = approve(publisher, ready)
    state = current(publisher)
    assert state['direct_sources'] == {'zipexample': source}
    assert published['installation'] == 'pending'
    assert intent(publisher)[0][0]['type'] == 'pip'  # managed projection, never legacy dispatch
    contribution = next(row for row in state['contributions'] if 'zipexample' in row['declaration'] or '@' in row['declaration'])
    assert contribution['declaration'] == 'ZipExample @ ' + URL and contribution['artifact_ref'] is None
    with publisher.engine.connect() as c:
        material = c.execute(select(publisher.tables['candidate_material'])).mappings().all()
    assert any(row['committed'] and row['path'].endswith('/' + source['digest']) for row in material)


def test_direct_ready_result_identity_and_retained_hash_bind(publisher, store):
    ready = ready_direct(publisher, store, DECLARATION, download=lambda url: wheel_bytes())
    source = ready[3]['zipexample']
    with publisher.engine.connect() as c:
        attestation = c.scalar(select(publisher.tables['candidate'].c.result).where(
            publisher.tables['candidate'].c.id == ready[0]))
    assert attestation['bundle']['inventory']['zipexample'] == {'version': '1.0', 'source_digest': source['digest']}
    for invalid in ('digest', 'artifacts', 'version', 'missing'):
        broken = deepcopy(attestation)
        if invalid == 'missing':
            broken['direct_sources'] = {}
        elif invalid == 'artifacts':
            broken['bundle']['artifacts'] = ['b' * 64]
        elif invalid == 'version':
            broken['bundle']['inventory']['zipexample']['version'] = '9'
        else:
            broken['direct_sources']['zipexample']['digest'] = '9' * 64
        with pytest.raises(PreparationError, match='invalid'):
            publisher._validate_result(ready[2].inputs, broken)


def test_direct_recorded_source_survives_unrelated_solve_and_update_advances(publisher, store):
    first = ready_direct(publisher, store, DECLARATION, download=lambda url: wheel_bytes())
    approve(publisher, first)
    finish(publisher)
    recorded = first[3]
    # An unrelated registry solve never re-fetches the recorded direct source.
    unrelated = publisher.prepare('1', 'Unrelated>=1', 'unrelated')
    claim = publisher.claim(unrelated)
    reused, _ = resolve_direct_sources(claim.inputs, store,
        download=lambda url: (_ for _ in ()).throw(AssertionError('re-fetch')))
    assert reused == recorded
    publisher.fail(claim)
    # Explicit Update may prepare new bytes as a new revision.
    update = ready_direct(publisher, store, 'zipexample', 'update', operation='update',
                          download=lambda url: wheel_bytes(version='2.0'))
    assert update[3]['zipexample']['version'] == '2.0'
    assert update[3]['zipexample']['digest'] != recorded['zipexample']['digest']
    approve(publisher, update, key='approve-update')
    state = snapshot(publisher)
    assert current(publisher)['direct_sources']['zipexample']['version'] == '2.0'
    prior = next(row for row in state['revision'] if row['snapshot'].get('direct_sources', {}).get('zipexample', {}).get('version') == '1.0')
    assert prior['snapshot']['direct_sources']['zipexample']['digest'] == recorded['zipexample']['digest']


def test_direct_changed_bytes_at_recorded_url_fail_closed(publisher, store):
    approve(publisher, ready_direct(publisher, store, DECLARATION, download=lambda url: wheel_bytes()))
    finish(publisher)
    with pytest.raises(PreparationError, match='content changed; explicit Update required'):
        ready_direct(publisher, store, DECLARATION, 'reinstall', download=lambda url: wheel_bytes(version='2.0'))


def test_direct_unavailable_upstream_fails_closed_without_substitution(publisher, store):
    from docassemble_executor.preparation_poller import PreparationPoller
    candidate = publisher.prepare('1', DECLARATION, 'prepare')

    class Unavailable:
        def build(self, inputs, *, retain_direct):
            return resolve_direct_sources(inputs, store, retain=retain_direct,
                download=lambda url: (_ for _ in ()).throw(PreparationError('direct URL artifact unavailable')))
    assert PreparationPoller(publisher, Unavailable(), artifact_root=store).run_once() == 'failed'
    with publisher.engine.connect() as c:
        assert c.scalar(select(publisher.tables['candidate'].c.state).where(
            publisher.tables['candidate'].c.id == candidate)) == 'failed'


def test_direct_remove_last_source_retains_history_not_identity(publisher, store):
    first = ready_direct(publisher, store, DECLARATION, download=lambda url: wheel_bytes())
    published = approve(publisher, first)
    finish(publisher)
    contribution = next(row for row in current(publisher)['contributions'] if '@' in row['declaration'])
    removal = ready_direct(publisher, store, 'zipexample', 'remove', operation='remove',
                           contribution_id=contribution['id'], download=lambda url: wheel_bytes())
    assert removal[3] == {}
    approve(publisher, removal, key='approve-remove')
    assert current(publisher)['direct_sources'] == {}
    assert not intent(publisher)[0][0]['active']
    assert any(row['id'] == published['revision_id'] and row['snapshot']['direct_sources'] for row in snapshot(publisher)['revision'])


@pytest.mark.parametrize('revocation', ['role', 'owner'])
def test_direct_current_authorization_rechecked_before_publication(publisher, store, revocation):
    from sqlalchemy import text
    first = ready_direct(publisher, store, DECLARATION, download=lambda url: wheel_bytes())
    if revocation == 'owner':
        approve(publisher, first)
        finish(publisher)
        first = ready_direct(publisher, store, 'zipexample', 'update', operation='update',
                             download=lambda url: wheel_bytes(version='2.0'))
    with publisher.authority_write() as c:
        c.execute(text('DELETE FROM test_user_roles WHERE user_id=1' if revocation == 'role' else
                       'DELETE FROM test_package_auth WHERE user_id=1'))
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        approve(publisher, first, key='approve-again')
    assert (snapshot(publisher), intent(publisher)) == before
    with publisher.engine.connect() as c:
        material = c.execute(select(publisher.tables['candidate_material']).where(
            publisher.tables['candidate_material'].c.candidate_id == first[0])).mappings().all()
    assert material and all(not row['committed'] for row in material)


def test_direct_cleanup_after_cancel_and_worker_exit_preserves_committed(publisher, store):
    ready = ready_direct(publisher, store, DECLARATION, download=lambda url: wheel_bytes())
    source = ready[3]['zipexample']
    publisher.cancel('1', ready[0])
    assert publisher.reconcile_artifacts(store) == 0
    assert (store / source['digest']).exists()
    publisher.worker_exited(ready[2].worker_id)
    assert publisher.reconcile_artifacts(store) == 1
    assert not (store / source['digest']).exists()
    # A committed artifact is never removed by a later cleanup pass.
    second = ready_direct(publisher, store, DECLARATION, 'committed', download=lambda url: wheel_bytes())
    approve(publisher, second, key='approve-committed')
    assert publisher.reconcile_artifacts(store) == 0
    assert (store / second[3]['zipexample']['digest']).exists()


def test_direct_status_preview_redacts_embedded_credentials(publisher, store):
    ready = ready_direct(publisher, store, CREDENTIAL, download=lambda url: wheel_bytes())
    status = publisher.status('1', ready[0])
    assert 'DISPOSABLE_SECRET_TOKEN' not in repr(status)
    assert status['preview']['direct_sources']['zipexample']['url'] == (
        'https://example.org/files/zip_example-1.0-py3-none-any.whl?REDACTED')
    with publisher.engine.connect() as c:
        stored = c.scalar(select(publisher.tables['candidate'].c.result).where(
            publisher.tables['candidate'].c.id == ready[0]))
    assert 'DISPOSABLE_SECRET_TOKEN' in stored['direct_sources']['zipexample']['url']  # supplied exception


def test_direct_api_prepare_routes_without_legacy_install_intent_or_secret(api_http):
    from test_managed_api_postgres import call
    h = api_http
    before = snapshot(h.service), intent(h.service)
    response = call(h, '/api/package', body={'pip': CREDENTIAL, 'idempotency_key': 'direct'})
    assert response.status_code == 202, response.json
    assert (snapshot(h.service), intent(h.service)) == before
    assert 'DISPOSABLE_SECRET_TOKEN' not in response.get_data(as_text=True)
    claim = h.service.claim(response.json['candidate_id'])
    assert claim.inputs['direct_source']['url'].startswith('https://user:DISPOSABLE_SECRET_TOKEN@')
    assert claim.inputs['requirement']['declaration'] == (
        'ZipExample @ https://example.org/files/zip_example-1.0-py3-none-any.whl?REDACTED')


@pytest.mark.parametrize('declaration', [
    '-e ZipExample @ https://example.org/files/zip_example.whl',
    'ZipExample @ file:///tmp/DISPOSABLE_SECRET_TOKEN.whl',
    'ZipExample @ https://example.org/files/DISPOSABLE_SECRET_TOKEN.tar.gz',
])
def test_direct_api_rejection_is_redacted_and_leaves_no_candidate(api_http, declaration):
    from test_managed_api_postgres import call
    h = api_http
    before = snapshot(h.service), intent(h.service)
    response = call(h, '/api/package', body={'pip': declaration, 'idempotency_key': 'direct-reject'})
    assert response.status_code == 409, response.json
    assert 'DISPOSABLE_SECRET_TOKEN' not in response.get_data(as_text=True)
    assert (snapshot(h.service), intent(h.service)) == before
    with h.service.engine.connect() as c:
        assert c.scalar(select(h.service.tables['candidate'].c.id)) is None


def wait_for(predicate):
    import time
    until = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < until, 'interleaving seam timed out'
        time.sleep(0.01)


def receive(fd):
    assert select_fds([fd], [], [], 10)[0], 'worker did not reach interleaving seam'
    assert os.read(fd, 1) == b'x'


def test_direct_retention_registration_serializes_reconciliation(publisher, store, monkeypatch):
    from docassemble.base import managed_artifacts
    from sqlalchemy import text
    import hashlib
    import threading
    candidate = publisher.prepare('1', DECLARATION, 'prepare')
    claim = publisher.claim(candidate)
    exposed, release = threading.Event(), threading.Event()
    retain = managed_artifacts.retain_artifact
    data = wheel_bytes()
    reference = hashlib.sha256(data).hexdigest()

    def paused_retain(*args):
        path = retain(*args)
        exposed.set()
        assert release.wait(10)
        return path

    monkeypatch.setattr(managed_artifacts, 'retain_artifact', paused_retain)
    environment = publisher._environment

    def marked_environment(connection):
        if threading.current_thread().name.startswith('reconcile'):
            connection.execute(text("SET LOCAL application_name = 'direct-registration-race'"))
        return environment(connection)

    monkeypatch.setattr(publisher, '_environment', marked_environment)

    def blocked():
        with publisher.engine.connect() as connection:
            return connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE "
                "application_name='direct-registration-race' AND wait_event_type='Lock'")) > 0

    with ThreadPoolExecutor(max_workers=1) as writer, ThreadPoolExecutor(max_workers=1, thread_name_prefix='reconcile') as cleaner:
        retained = writer.submit(publisher.retain_direct_artifact, claim, store / '..' / store.name,
                                 reference, io.BytesIO(data))
        assert exposed.wait(10)
        cleanup = cleaner.submit(publisher.reconcile_artifacts, store)
        try:
            wait_for(blocked)
            assert (store / reference).exists()
        finally:
            release.set()
        assert retained.result(timeout=10) == store / reference
        assert cleanup.result(timeout=10) == 0
    with publisher.engine.connect() as connection:
        rows = connection.execute(select(publisher.tables['candidate_material'])).mappings().all()
    assert len(rows) == 1 and rows[0]['path'] == str(store / reference)


@contextmanager
def running_dispatcher(poller):
    """Real dispatcher process, with an independent database pool after fork."""
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read)
        try:
            poller.preparation.engine.dispose(close=False)
            os.write(write, poller.run_once().encode())
            os._exit(0)
        except BaseException:
            os._exit(1)
    os.close(write)
    reaped = False

    def result():
        nonlocal reaped
        assert select_fds([read], [], [], 10)[0], 'dispatcher did not finish'
        value = os.read(read, 32).decode()
        _, status = os.waitpid(pid, 0)
        reaped = True
        assert status == 0
        return value

    try:
        yield SimpleNamespace(pid=pid, result=result)
    finally:
        if not reaped:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
        os.close(read)


@pytest.mark.parametrize('ending', ['cancel-descendant', 'builder-crash'])
def test_direct_dispatcher_reconciles_fetch_build_and_descendant_exit(publisher, store, ending):
    from docassemble_executor.preparation_poller import PreparationPoller
    import hashlib
    candidate = publisher.prepare('1', DECLARATION, 'prepare')
    data = wheel_bytes()
    reference = hashlib.sha256(data).hexdigest()
    arrived, notify = os.pipe()
    release, resume = os.pipe()

    class DirectBuilder:
        def build(self, inputs, *, retain_direct):
            def download(url):
                os.write(notify, b'x')  # Fetch is outside the DB transaction.
                os.read(release, 1)
                return data
            sources, _ = resolve_direct_sources(inputs, store, download=download, retain=retain_direct)
            os.write(notify, b'x')  # Registered, but building is still live.
            os.read(release, 1)
            if ending == 'builder-crash':
                os._exit(17)
            # Builder returns while a detached double-fork descendant still uses bytes.
            if os.fork() == 0:
                os.setsid()
                if os.fork() != 0:
                    os._exit(0)
                os.write(notify, b'x')
                os.read(release, 1)
                assert (store / reference).read_bytes() == data
                os._exit(0)
            return direct_result(publisher, inputs, sources)

    poller = PreparationPoller(publisher, DirectBuilder(), artifact_root=store)
    try:
        with running_dispatcher(poller) as running:
            try:
                receive(arrived)
                assert publisher.reconcile_artifacts(store) == 0
                assert not (store / reference).exists()
                os.write(resume, b'x')
                receive(arrived)
                assert (store / reference).exists()
                assert publisher.reconcile_artifacts(store) == 0
                if ending == 'cancel-descendant':
                    os.write(resume, b'x')
                    receive(arrived)
                    publisher.cancel('1', candidate)
                    poller._reconcile()
                    assert (store / reference).exists()
                    with publisher.engine.connect() as connection:
                        assert connection.scalar(select(publisher.tables['preparation_worker'].c.exited)) is False
                os.write(resume, b'x')
                assert running.result() == ('fenced' if ending == 'cancel-descendant' else 'failed')
            finally:
                os.write(resume, b'xxx')
        assert not (store / reference).exists()
        with publisher.engine.connect() as connection:
            assert connection.scalar(select(publisher.tables['preparation_worker'].c.exited)) is True
            assert connection.scalar(select(publisher.tables['candidate_material'].c.deleted)) is True
    finally:
        for fd in (arrived, notify, release, resume):
            os.close(fd)


def test_direct_dispatcher_ready_then_cancel_automatically_cleans(publisher, store):
    from docassemble_executor.preparation_poller import PreparationPoller

    class DirectBuilder:
        def build(self, inputs, *, retain_direct):
            sources, _ = resolve_direct_sources(inputs, store, download=lambda url: wheel_bytes(), retain=retain_direct)
            return direct_result(publisher, inputs, sources)

    candidate = publisher.prepare('1', DECLARATION, 'prepare')
    poller = PreparationPoller(publisher, DirectBuilder(), artifact_root=store)
    assert poller.run_once() == 'ready'
    with publisher.engine.connect() as connection:
        assert connection.scalar(select(publisher.tables['preparation_worker'].c.exited)) is True
    assert len(list(store.glob('[a-f0-9]' * 64))) == 1
    publisher.cancel('1', candidate)
    assert poller.run_once() == 'idle'
    assert not list(store.glob('[a-f0-9]' * 64))


def test_direct_dispatcher_holds_receipt_lock_through_terminal_write(publisher, store, monkeypatch):
    from docassemble_executor.preparation_poller import PreparationPoller
    arrived, notify = os.pipe()
    release, resume = os.pipe()
    publish = publisher.publish_ready

    class DirectBuilder:
        def build(self, inputs, *, retain_direct):
            sources, _ = resolve_direct_sources(inputs, store, download=lambda url: wheel_bytes(), retain=retain_direct)
            return direct_result(publisher, inputs, sources)

    def paused_publish(claim, result):
        os.write(notify, b'x')
        os.read(release, 1)
        return publish(claim, result)

    monkeypatch.setattr(publisher, 'publish_ready', paused_publish)
    candidate = publisher.prepare('1', DECLARATION, 'prepare')
    poller = PreparationPoller(publisher, DirectBuilder(), artifact_root=store)
    try:
        with running_dispatcher(poller) as running:
            try:
                receive(arrived)
                assert list((store / '.direct-workers').glob('*/exit.json'))
                publisher.cancel('1', candidate)
                poller._reconcile()
                with publisher.engine.connect() as connection:
                    assert connection.scalar(select(publisher.tables['preparation_worker'].c.exited)) is False
                assert list(store.glob('[a-f0-9]' * 64))
            finally:
                os.write(resume, b'x')
            assert running.result() == 'fenced'
        assert not list(store.glob('[a-f0-9]' * 64))
        with publisher.engine.connect() as connection:
            assert connection.scalar(select(publisher.tables['preparation_worker'].c.exited)) is True
    finally:
        for fd in (arrived, notify, release, resume):
            os.close(fd)


def test_direct_dispatcher_crash_leaves_guardian_to_prove_exit(publisher, store):
    import ctypes
    from docassemble_executor.preparation_poller import PreparationPoller
    from docassemble_executor.preparation_processes import _subreaper
    # Adopt the orphan guardian in this test process so it can be reaped without
    # relying on the host's PID 1. This is test teardown, never exit authority.
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    _subreaper()
    arrived, notify = os.pipe()
    release, resume = os.pipe()
    guardian_read, guardian_write = os.pipe()
    guardian = None

    class DirectBuilder:
        def build(self, inputs, *, retain_direct):
            sources, _ = resolve_direct_sources(inputs, store, download=lambda url: wheel_bytes(), retain=retain_direct)
            os.write(guardian_write, str(os.getppid()).encode())
            os.write(notify, b'x')
            os.read(release, 1)
            return direct_result(publisher, inputs, sources)

    candidate = publisher.prepare('1', DECLARATION, 'prepare')
    poller = PreparationPoller(publisher, DirectBuilder(), artifact_root=store)
    try:
        with running_dispatcher(poller) as running:
            receive(arrived)
            guardian = int(os.read(guardian_read, 32))
            os.kill(running.pid, signal.SIGKILL)
            publisher.cancel('1', candidate)
            poller._reconcile()
            assert not list((store / '.direct-workers').glob('*/exit.json'))
            assert list(store.glob('[a-f0-9]' * 64))
            with publisher.engine.connect() as connection:
                assert connection.scalar(select(publisher.tables['preparation_worker'].c.exited)) is False
            os.write(resume, b'x')
            wait_for(lambda: bool(list((store / '.direct-workers').glob('*/exit.json'))))
        _, status = os.waitpid(guardian, 0)
        guardian = None
        assert status == 0
        # A replacement dispatcher consumes only the durable receipt, not a PID.
        replacement = PreparationPoller(publisher, DirectBuilder(), artifact_root=store)
        assert replacement.run_once() == 'idle'
        assert not list(store.glob('[a-f0-9]' * 64))
        with publisher.engine.connect() as connection:
            assert connection.scalar(select(publisher.tables['preparation_worker'].c.exited)) is True
    finally:
        os.write(resume, b'x')
        if guardian is not None:
            os.waitpid(guardian, 0)
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0
        for fd in (arrived, notify, release, resume, guardian_read, guardian_write):
            os.close(fd)


@pytest.mark.parametrize('failure', ['guardian-crash', 'missing-proof', 'unsupported-platform'])
def test_direct_dispatcher_without_exit_authority_fails_closed(publisher, store, monkeypatch, failure):
    from docassemble_executor import preparation_processes
    from docassemble_executor.preparation_containers import ContainerWorkers
    from docassemble_executor.preparation_poller import PreparationPoller

    class DirectBuilder:
        def build(self, inputs, *, retain_direct):
            sources, _ = resolve_direct_sources(inputs, store, download=lambda url: wheel_bytes(), retain=retain_direct)
            return direct_result(publisher, inputs, sources)

    if failure == 'guardian-crash':
        monkeypatch.setattr(preparation_processes, '_subreaper', lambda: os.kill(os.getpid(), signal.SIGKILL))
    elif failure == 'missing-proof':
        write = ContainerWorkers._write
        monkeypatch.setattr(ContainerWorkers, '_write', staticmethod(
            lambda path, value: None if path.name == 'exit.json' else write(path, value)))
    else:
        monkeypatch.setattr(preparation_processes.sys, 'platform', 'unsupported')
    candidate = publisher.prepare('1', DECLARATION, 'prepare')
    poller = PreparationPoller(publisher, DirectBuilder(), artifact_root=store)
    assert poller.run_once() == 'failed'
    assert poller.run_once() == 'idle'
    with publisher.engine.connect() as connection:
        assert connection.scalar(select(publisher.tables['candidate'].c.state).where(
            publisher.tables['candidate'].c.id == candidate)) == 'failed'
        assert connection.scalar(select(publisher.tables['preparation_worker'].c.exited)) is False
    if failure == 'missing-proof':
        assert list(store.glob('[a-f0-9]' * 64))  # No receipt means no cleanup, even after builder/guardian exit.
