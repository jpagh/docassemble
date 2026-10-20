"""Source-named regressions at proof seams; no containers, SQL, network or uv."""
import ast
import base64
from contextlib import nullcontext
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import pytest
from sqlalchemy import select, text

from docassemble.base.managed_publication import Publication
from docassemble.base.managed_records import preparation_tables


PREPARATION = Path(__file__).resolve().parents[2] / 'preparation'


def proof_tree(name):
    return ast.parse((PREPARATION / (name + '.py')).read_text())


def execute(nodes, namespace):
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<proof seam>', 'exec'), namespace)


def diagnostics():
    path = PREPARATION / 'frozen_apply_diagnostics.py'
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.frozen_apply_failure


@pytest.mark.parametrize('previous', [None, '', '/configured/worker/artifacts'])
@pytest.mark.parametrize('failure', [None, 'prepare_zip', 'approve'])
def test_playground_install_proof_matches_publication_store_and_restores_environment(
        monkeypatch, tmp_path, previous, failure):
    if previous is None:
        monkeypatch.delenv('DA_EXECUTOR_ARTIFACT_ROOT', raising=False)
    else:
        monkeypatch.setenv('DA_EXECUTOR_ARTIFACT_ROOT', previous)
    tree = proof_tree('playground_install_proof')
    preparation = next(node for node in ast.walk(tree) if isinstance(node, ast.Try) and
                       any(isinstance(child, ast.Name) and child.id == 'previous_artifact_root'
                           for statement in node.finalbody for child in ast.walk(statement)))
    previous_assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign) and
                               isinstance(node.targets[0], ast.Name) and
                               node.targets[0].id == 'previous_artifact_root')
    override = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign) and
                    isinstance(node.targets[0], ast.Subscript) and
                    isinstance(node.targets[0].slice, ast.Constant) and
                    node.targets[0].slice.value == 'DA_EXECUTOR_ARTIFACT_ROOT')
    store = tmp_path / 'artifacts'
    core = {'packaging': {'version': '25.0', 'source_digest': 'core'}}
    result = {'bundle': {'inventory': {**core, 'docassemble-example': {'version': '1.0'}}},
              'desired_diff': {'docassemble-example': {'before': None, 'after': {'version': '1.0'}}},
              'runtime_diff': {'docassemble-example': {'before': None, 'after': {'version': '1.0'}}}}
    stages = []
    def stage(name):
        stages.append(name)
        assert os.environ['DA_EXECUTOR_ARTIFACT_ROOT'] == str(store)
        assert Publication._artifact_material_matches(
            {'inputs': {'material_stores': {'artifact': str(store)}}},
            {'path': str(store / 'digest')}, 'digest')
        if name == failure:
            raise RuntimeError('injected ' + name)
    def prepare_zip(*args, artifact_root, **kwargs):
        stage('prepare_zip')
        assert artifact_root == store
        return 'candidate'
    def claim(*args, **kwargs):
        stage('claim')
        return SimpleNamespace(inputs={})
    def builder(*args, artifact_root, **kwargs):
        assert artifact_root == store
        return SimpleNamespace(build=lambda _: (stage('build'), result)[1])
    service = SimpleNamespace(
        prepare_zip=prepare_zip, claim=claim,
        publish_ready=lambda *args: (stage('publish_ready'), True)[1],
        status=lambda *args: (stage('status'), {'approval_supported': True, 'result_digest': 'result'})[1],
        approve=lambda *args: (stage('approve'), {'revision_id': 'published'})[1])
    namespace = dict(os=os, root=tmp_path, store=store, core=core, service=service, uv='uv',
                     Path=Path, RegistryBuilder=builder, select=select, text=text,
                     tables=preparation_tables('proof_'),
                     engine=SimpleNamespace(connect=lambda: nullcontext(SimpleNamespace(
                         scalar=lambda query: 0 if 'count(*)' in str(query) else 'baseline'))),
                     generator=lambda _: {'make_package_zip': lambda *args, **kwargs:
                                          nullcontext(SimpleNamespace(name=str(tmp_path / 'package.zip')))},
                     package_info=lambda **kwargs: kwargs, author_info=lambda: {})
    if failure:
        with pytest.raises(RuntimeError, match='injected ' + failure):
            execute([previous_assignment, override, preparation], namespace)
    else:
        execute([previous_assignment, override, preparation], namespace)
        assert stages == ['prepare_zip', 'claim', 'build', 'publish_ready', 'status', 'approve']
    assert os.environ.get('DA_EXECUTOR_ARTIFACT_ROOT') == previous


@pytest.mark.parametrize('proof', ['git_install_proof', 'private_git_proof'])
@pytest.mark.parametrize('returncode', [0, 2])
def test_git_install_proof_and_private_git_proof_apply_explicit_builder_python_and_expose_failure(
        monkeypatch, tmp_path, proof, returncode):
    monkeypatch.setenv('PROOF_SECRET_TOKEN', 'must-not-be-inherited')
    main = next(node for node in proof_tree(proof).body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    applied = next(node for node in ast.walk(main) if isinstance(node, ast.Assign) and
                   isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'applied')
    environment = next(node for node in ast.walk(main) if isinstance(node, ast.Assign) and
                       isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'env')
    assertion = next(node for node in ast.walk(main) if isinstance(node, ast.Assert) and
                     isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Attribute) and
                     isinstance(node.test.left.value, ast.Name) and node.test.left.value.id == 'applied')
    calls = []
    token, rotated = 'DISPOSABLE_PRIVATE_GIT_TOKEN', 'DISPOSABLE_ROTATED_GIT_TOKEN'
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv == ['uv', 'sync', '--frozen', '--no-build', '--offline', '--no-config',
                        '--python', sys.executable]
        assert kwargs['cwd'] == tmp_path / 'bundle'
        assert kwargs['env'] == {'PATH': os.environ['PATH'], 'HOME': str(tmp_path),
                                 'UV_PROJECT_ENVIRONMENT': str(tmp_path / 'runtime'),
                                 'UV_CACHE_DIR': str(tmp_path / 'empty-cache'), 'UV_PYTHON_DOWNLOADS': 'never'}
        assert kwargs['text'] and kwargs['capture_output']
        return subprocess.CompletedProcess(argv, returncode, 'uv stdout\n',
                                           'interpreter unavailable\nhttps://user:secret@host/path?token=value\n' +
                                           (token + '\n' + rotated if proof == 'private_git_proof' else ''))
    namespace = dict(os=os, sys=sys, root=tmp_path, runtime=tmp_path / 'runtime', uv='uv',
                     bundle=tmp_path / 'bundle', subprocess=SimpleNamespace(run=run),
                     frozen_apply_failure=diagnostics(), TOKEN=token, ROTATED=rotated)
    if returncode:
        with pytest.raises(AssertionError) as error:
            execute([environment, applied, assertion], namespace)
        output = str(error.value)
        assert 'exit 2' in output and 'stdout:\nuv stdout' in output
        assert 'stderr:\ninterpreter unavailable' in output
        assert all(secret not in output for secret in ('user:secret', 'token=value', token, rotated))
    else:
        execute([environment, applied, assertion], namespace)
    assert len(calls) == 1


def test_frozen_apply_diagnostics_redacts_credentials_in_both_streams_without_hiding_uv_error():
    token = 'private/token value'
    encoded = base64.b64encode((token + ':').encode()).decode()
    applied = subprocess.CompletedProcess([], 2,
        'stdout context\n' + token + '\n' + quote(token, safe='') + '\n' + quote_plus(token) + '\n' + encoded +
        '\nAuthorization: Basic ' + encoded,
        'No interpreter found\nhttps://user:password@example.invalid/wheel?token=secret\n'
        'Authorization: Bearer unrelated-secret\nhttps://user:password@host:bad/path')
    output = diagnostics()(applied, 'cold offline apply failed', credentials=(token,))
    assert 'exit 2' in output and 'stdout context' in output and 'No interpreter found' in output
    assert 'https://example.invalid/wheel?REDACTED' in output and '[redacted URL]' in output
    assert all(secret not in output for secret in
               (token, quote(token, safe=''), quote_plus(token), encoded, 'password', 'token=secret', 'unrelated-secret'))


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
@pytest.mark.parametrize('source,redacted,secrets', [
    ('{"Authorization": "Bearer unrelated-secret"}',
     '{"Authorization": "[credential]"}', ('Bearer', 'unrelated-secret')),
    ('Authorization: Digest username="alice", response="secret"',
     'Authorization: [credential]', ('Digest', 'alice', 'secret')),
    (r'https:\/\/user:password@example.invalid\/wheel?token=secret',
     'https://example.invalid/wheel?REDACTED', ('user', 'password', 'token=secret')),
    ('{"outer": {"inner": {"Authorization": "Negotiate unrelated-secret"}}}',
     '{"outer": {"inner": {"Authorization": "[credential]"}}}', ('Negotiate', 'unrelated-secret')),
    ('Authorization: Digest username="alice",\r\n\tresponse="secret",\r\n nonce="hidden"\r\ncontext',
     'Authorization: [credential]\r\ncontext', ('Digest', 'alice', 'secret', 'hidden')),
    ('AUTHORIZATION=Custom unrelated-secret\nproxy-authorization: Negotiate hidden\ncontext',
     'AUTHORIZATION=[credential]\nproxy-authorization: [credential]\ncontext',
     ('Custom', 'unrelated-secret', 'Negotiate', 'hidden')),
    ("{'aUtHoRiZaTiOn': 'Bearer unrelated-secret', 'pRoXy-AuThOrIzAtIoN': 'Basic hidden'}",
     "{'aUtHoRiZaTiOn': '[credential]', 'pRoXy-AuThOrIzAtIoN': '[credential]'}",
     ('Bearer', 'unrelated-secret', 'Basic', 'hidden')),
    ('Authorization: Digest username="alice", realm="restricted", nonce="hidden", response="secret"',
     'Authorization: [credential]', ('Digest', 'alice', 'restricted', 'hidden', 'secret')),
    (r'{"url": "https:\/\/user:password@example.invalid\/wheel?token=secret"}',
     '{"url": "https://example.invalid/wheel?REDACTED"}', ('user', 'password', 'token=secret')),
    (r'{"url": "https:\u002f\u002Fuser:password@example.invalid\u002fwheel?token=secret"}',
     '{"url": "https://example.invalid/wheel?REDACTED"}', ('user', 'password', 'token=secret')),
    (r'{"Authorization": "Digest username=\"alice\", response=\"secret\""}',
     '{"Authorization": "[credential]"}', ('Digest', 'alice', 'secret')),
    ('fetch failed: user:password@example.invalid/wheel?token=secret',
     'fetch failed: [redacted URL]', ('user', 'password', 'token=secret')),
    ('fetch failed: https:/user:password@example.invalid/wheel?token=secret',
     'fetch failed: https:/[redacted URL]', ('user', 'password', 'token=secret')),
    ('fetch failed: user:p=a,s;s@example.invalid/wheel?token=secret',
     'fetch failed: [redacted URL]', ('user', 'p=a,s;s', 'token=secret')),
    ('Authorization:\n  Bearer unrelated-secret\ncontext',
     'Authorization:\n  [credential]\ncontext', ('Bearer', 'unrelated-secret')),
    ('{"Authorization":\n "Bearer unrelated-secret"}',
     '{"Authorization":\n "[credential]"}', ('Bearer', 'unrelated-secret')),
    ('Authorization: "Bearer unrelated-secret\nhidden"\ncontext',
     'Authorization: "[credential]"\ncontext', ('Bearer', 'unrelated-secret', 'hidden')),
    ('X-Authorization: Bearer unrelated-secret\ncontext',
     'X-Authorization: [credential]\ncontext', ('Bearer', 'unrelated-secret')),
    ('X-Auth-Token: unrelated-secret\nx-AuTh-KeY=hidden\ncontext',
     'X-Auth-Token: [credential]\nx-AuTh-KeY=[credential]\ncontext', ('unrelated-secret', 'hidden')),
    ('{"X-Authorization": "Basic unrelated-secret", "X-Auth-Token": "hidden"}',
     '{"X-Authorization": "[credential]", "X-Auth-Token": "[credential]"}',
     ('Basic', 'unrelated-secret', 'hidden')),
    ('Authorization: "Bearer unrelated-secret"\r\n\thidden\r\ncontext',
     'Authorization: "[credential]"\r\ncontext', ('Bearer', 'unrelated-secret', 'hidden')),
    ('Authorization: "Bearer unrelated-secret" \t\r\n\thidden\r\ncontext',
     'Authorization: "[credential]"\r\ncontext', ('Bearer', 'unrelated-secret', 'hidden')),
    ('Authorization: "Bearer unrelated-secret"; response="hidden"\ncontext',
     'Authorization: "[credential]"\ncontext', ('Bearer', 'unrelated-secret', 'hidden')),
    ('"Authorization": "Bearer unrelated-secret"; response="hidden"\ncontext',
     '"Authorization": "[credential]"\ncontext', ('Bearer', 'unrelated-secret', 'hidden')),
    ('{"Authorization": "Bearer unrelated-secret", "context": "safe context"}',
     '{"Authorization": "[credential]", "context": "safe context"}', ('Bearer', 'unrelated-secret')),
    ('Authorization: Digest username="a}lice", response="secret"\ncontext',
     'Authorization: [credential]\ncontext', ('Digest', 'a}lice', 'secret')),
    ('Authorization: "Bearer unrelated-secret"; X-Auth-Token: hidden\ncontext',
     'Authorization: "[credential]"; X-Auth-Token: [credential]\ncontext',
     ('Bearer', 'unrelated-secret', 'hidden')),
    ('Set-Cookie: session=unrelated-secret; Path=/; Domain=example.invalid; '
     'Expires=Wed, 21 Oct 2026 07:28:00 GMT; HttpOnly; Secure; SameSite=Strict\ncontext',
     'Set-Cookie: session=[credential]; Path=/; Domain=example.invalid; '
     'Expires=Wed, 21 Oct 2026 07:28:00 GMT; HttpOnly; Secure; SameSite=Strict\ncontext',
     ('unrelated-secret',)),
    ('Cookie: session=unrelated-secret; token="hidden"\ncontext',
     'Cookie: session=[credential]; token="[credential]"\ncontext', ('unrelated-secret', 'hidden')),
    ('Cookie: session=unrelated-secret;\r\n token=hidden\ncontext',
     'Cookie: session=[credential];\r\n token=[credential]\ncontext', ('unrelated-secret', 'hidden')),
    ('{"Set-Cookie": "session=unrelated-secret; Path=/; HttpOnly", "Cookie": "token=hidden"}',
     '{"Set-Cookie": "session=[credential]; Path=/; HttpOnly", "Cookie": "token=[credential]"}',
     ('unrelated-secret', 'hidden')),
    ('Cookie: Path=unrelated-secret; Domain=hidden\ncontext',
     'Cookie: Path=[credential]; Domain=[credential]\ncontext', ('unrelated-secret', 'hidden')),
    ('Set-Cookie: Path=unrelated-secret; Path=/; HttpOnly\ncontext',
     'Set-Cookie: Path=[credential]; Path=/; HttpOnly\ncontext', ('unrelated-secret',)),
    ("context: Failed to fetch 'https://user:password@example.invalid/wheel?token=secret'.",
     "context: Failed to fetch 'https://example.invalid/wheel?REDACTED'.", ('user', 'password', 'token=secret')),
    ('context: unrelated-secret@example.invalid/wheel?token=hidden',
     'context: [redacted URL]', ('unrelated-secret', 'token=hidden')),
])
def test_frozen_apply_diagnostics_redacts_unexpected_output_shapes(stream, source, redacted, secrets):
    token = 'fixture/token value'
    streams = {'stdout': 'stdout context', 'stderr': 'stderr context'}
    streams[stream] = source + '\nfixture=' + token
    applied = subprocess.CompletedProcess([], 2, **streams)
    output = diagnostics()(applied, 'cold offline apply failed', credentials=(token,))
    assert stream + ':\n' + redacted + '\nfixture=[credential]' in output
    assert all(secret not in output for secret in secrets)
    assert token not in output
    assert 'exit 2' in output and 'context' in output


def test_frozen_apply_diagnostics_redacts_credentials_in_failure_message():
    token = 'fixture-token'
    applied = subprocess.CompletedProcess([], 2, 'stdout context', 'stderr context')
    output = diagnostics()(applied, 'Authorization: Custom secret\nfixture=' + token, credentials=(token,))
    assert output.startswith('Authorization: [credential]\nfixture=[credential] (exit 2)')
    assert 'secret' not in output and token not in output


def test_frozen_apply_diagnostics_normalizes_fixture_credentials_without_leaking_them():
    token = 'fixture/token value'
    applied = subprocess.CompletedProcess([], 2, r'fixture=fixture\/token value', 'safe context')
    output = diagnostics()(applied, 'cold offline apply failed', credentials=(token,))
    assert 'stdout:\nfixture=[credential]' in output
    assert token not in output and 'fixture/token value' not in output


@pytest.mark.parametrize('source', [
    'Authorization: "unterminated secret',
    '{"Authorization": "unterminated secret}', b'secret',
    'Authorization: Bearer first\\\nsecond-secret',
    'Authorization: "Bearer first"\\\nsecond-secret',
    'Authorization: Bearer first\rsecond-secret',
    'Authorization: "Bearer first"\rsecond-secret',
    'Authorization:\nBearer secret',
    r'"{\"Authorization\": \"Bearer secret\"}"',
    r'{"\u0041uthorization": "Bearer secret"}',
    r'{"url":"https:\/\/secret\u0040example.invalid\/wheel?token=hidden"}',
    'https%3A%2F%2Fexample.invalid/wheel?token=secret',
    'https%3A%2F%2Fuser%3Apassword%40example.invalid%2Fwheel',
    r'{"url":"https://secret\uZZZZexample.invalid/wheel"}',
])
def test_frozen_apply_diagnostics_fails_closed_on_unredactable_stream(source):
    applied = subprocess.CompletedProcess([], 2, 'safe context', source)
    output = diagnostics()(applied, 'cold offline apply failed', credentials=('fixture-token',))
    assert output == ('cold offline apply failed (exit 2)\n'
                      'stdout:\nsafe context\nstderr:\n[redacted diagnostic]')
    assert 'secret' not in output and 'fixture-token' not in output


@pytest.mark.parametrize('credential', ['fixture/token value', 'user:pass'])
@pytest.mark.parametrize('suffix', ['', ':'])
def test_frozen_apply_diagnostics_redacts_standalone_base64_credentials(credential, suffix):
    encoded = base64.b64encode((credential + suffix).encode()).decode()
    applied = subprocess.CompletedProcess([], 2, 'context\nfixture=' + encoded, 'safe context')
    output = diagnostics()(applied, 'cold offline apply failed', credentials=(credential,))
    assert output == ('cold offline apply failed (exit 2)\n'
                      'stdout:\ncontext\nfixture=[credential]\nstderr:\nsafe context')
    assert encoded not in output and credential not in output


def test_frozen_apply_diagnostics_fails_closed_when_url_redaction_raises(monkeypatch):
    redact = diagnostics()
    def fail(_):
        raise RuntimeError('source secret must not escape')
    monkeypatch.setitem(redact.__globals__, 'redact_direct_url', fail)
    applied = subprocess.CompletedProcess([], 2, 'https://user:secret@host/path', 'safe context')
    output = redact(applied, 'cold offline apply failed', credentials=('fixture-token',))
    assert output == ('cold offline apply failed (exit 2)\n'
                      'stdout:\n[redacted diagnostic]\nstderr:\nsafe context')
    assert 'secret' not in output and 'fixture-token' not in output
