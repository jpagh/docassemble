"""Real HTTPS Basic-auth source -> pinned uv offline private wheel preparation."""
import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import zipfile

from docassemble_executor.preparation import PreparationError
from docassemble_executor.private_index import PrivateIndexSnapshot
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained


def wheel(name, dependency=None):
    stem = name.replace('-', '_')
    info = stem + '-1.0.dist-info/'
    files = {stem + '/__init__.py': b'VALUE = 1\n',
             info + 'METADATA': ('Metadata-Version: 2.3\nName: ' + name + '\nVersion: 1.0\n'
                                'Provides-Extra: feature\n' +
                                ('Requires-Dist: ' + dependency + '\n' if dependency else '')).encode(),
             info + 'WHEEL': b'Wheel-Version: 1.0\nGenerator: proof\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    records = [key + ',sha256=' + base64.urlsafe_b64encode(hashlib.sha256(value).digest()).decode().rstrip('=') + ',' + str(len(value))
               for key, value in files.items()]
    files[info + 'RECORD'] = ('\n'.join(records + [info + 'RECORD,,']) + '\n').encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return stem + '-1.0-py3-none-any.whl', output.getvalue()


def main():
    evidence = Path('/tmp/proof')
    evidence.mkdir()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        certificate, key = root / 'ca.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                        '-out', str(certificate), '-days', '1', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
        secret = 'DISPOSABLE_PRIVATE_TOKEN'
        reference = root / 'credential.json'
        wanted = 'Basic ' + base64.b64encode(('fixture:' + secret).encode()).decode()
        other_reference = root / 'other.json'
        other_wanted = 'Basic ' + base64.b64encode(b'other:OTHER_DISPOSABLE_TOKEN').decode()
        packages = dict(wheel(name, dependency) for name, dependency in
                        [('private-one', 'private-two==1.0'), ('private-two', None), ('private-core', None),
                         ('public-wanted', 'six==1.17.0')])
        requests = []
        mode = {'value': 'normal'}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                auth = self.headers.get('Authorization')
                source = 'beta' if self.path.startswith('/beta/') else 'alpha'
                requests.append({'path': self.path, 'source': source,
                                 'credential_matches_source': auth == (other_wanted if source == 'beta' else wanted)})
                assert not (source == 'beta' and auth == wanted), 'cross-source credential exposure'
                if mode['value'] in ('401', '403'):
                    self.send_error(int(mode['value']))
                    return
                if auth != (other_wanted if source == 'beta' else wanted):
                    self.send_error(401)
                    return
                if mode['value'] == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', '/beta/simple/private-one/')
                    self.end_headers()
                    return
                filename = self.path.rsplit('/', 1)[-1]
                if filename in packages:
                    data = packages[filename]
                else:
                    name = self.path.rstrip('/').rsplit('/', 1)[-1]
                    filename = name.replace('-', '_') + '-1.0-py3-none-any.whl'
                    if filename not in packages:
                        self.send_error(404)
                        return
                    link = filename + '#sha256=' + hashlib.sha256(packages[filename]).hexdigest()
                    if mode['value'] == 'cross-source':
                        link = '/beta/simple/' + name + '/' + link
                    if mode['value'] == 'userinfo':
                        link = 'https://fixture:' + secret + '@localhost/a.whl'
                    if mode['value'] == 'token':
                        link = 'https://' + secret + '@localhost/a.whl'
                    if mode['value'] == 'query':
                        link = filename + '?token=' + secret + '#sha256=' + hashlib.sha256(packages[filename]).hexdigest()
                    data = ('<a href="' + link + '">wheel</a>').encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        index = 'https://localhost:' + str(server.server_port) + '/alpha/simple/'
        reference.write_text(json.dumps({'index_url': index, 'username': 'fixture', 'password': secret}))
        reference.chmod(0o600)
        other_reference.write_text(json.dumps({'index_url': index.replace('/alpha/', '/beta/'),
                                               'username': 'other', 'password': 'OTHER_DISPOSABLE_TOKEN'}))
        other_reference.chmod(0o600)
        core = {'private-core': {'version': '1.0', 'source_digest': hashlib.sha256(packages['private_core-1.0-py3-none-any.whl']).hexdigest()}}
        inputs = {'baseline': {'core': core, 'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
                  'config': {'private_indexes': {'alpha': index},
                             'private_bindings': {'private-one': 'alpha', 'private-two': 'alpha', 'private-core': 'alpha'}},
                  'desired_inventory': core, 'runtime': {'kind': 'normal', 'inventory': core}},
                  'contributions': [], 'requirement': {'declaration': 'private-one[feature]>=1; python_version >= "3.12"'},
                  'ownership_effects': {'normalized_name': 'private-one', 'owner_id': 'alice', 'existing': False}}
        snapshot = PrivateIndexSnapshot({'alpha': reference}, ca_file=certificate)
        builder = RegistryBuilder(evidence / 'retained', '/usr/local/libexec/uv-pinned', private_snapshot=snapshot)
        original = copy.deepcopy(inputs)
        try:
            result = builder.build(inputs)
            assert inputs == original
            assert set(result['bundle']['inventory']) == {'private-one', 'private-two', 'private-core'}
            assert result['bundle']['inventory']['private-core'] == core['private-core']
            assert result['warnings'] == ['private-source:private-core:alpha', 'private-source:private-one:alpha', 'private-source:private-two:alpha']
            retained = builder.store / result['bundle']['digest']
            verify_retained(retained, result['bundle']['digest'])
            assert json.loads((retained / 'private-sources.json').read_text())[0]['url'] == index
            assert original['requirement']['declaration'] in json.loads(
                '[' + (retained / 'solve-project.toml').read_text().split('dependencies = [', 1)[1].split(']\n', 1)[0] + ']')
            (evidence / 'attestation.json').write_text(json.dumps(result, indent=2))
            print('PASS pinned uv 0.12.21 private wheel install, exact declaration, explicit transitive binding, retained bytes and cold-cache offline frozen sync', flush=True)
            # A second unattended preparation reuses a reference, not a returned secret.
            repeated = builder.build(inputs)
            assert repeated['bundle']['inventory'] == result['bundle']['inventory']
            count = len(requests)
            wrong_scope = copy.deepcopy(inputs)
            wrong_scope['baseline']['config']['private_indexes']['alpha'] = index.replace('/alpha/', '/beta/')
            try:
                builder.build(wrong_scope)
            except PreparationError as error:
                assert 'exact source scope' in str(error)
            else:
                raise AssertionError('reference reused for a different source')
            assert len(requests) == count
            builder._run(['sync', '--frozen', '--no-build', '--offline', '--no-config'],
                         retained, root / 'relocated-cache', root / 'relocated-venv')
            verify_retained(retained, result['bundle']['digest'])
            print('PASS unattended reference reuse without disclosure; changed source denied before request; relocated retained frozen sync', flush=True)
            for failure in ('401', '403', 'redirect', 'cross-source', 'userinfo', 'token', 'query'):
                mode['value'] = failure
                count = len(requests)
                try:
                    builder.build(inputs)
                except PreparationError as error:
                    assert secret not in str(error) and 'OTHER_DISPOSABLE_TOKEN' not in str(error)
                    if failure in ('401', '403'):
                        assert '401/403' in str(error)
                    if failure == 'query':
                        assert 'no sdists or URL secrets' in str(error)
                else:
                    raise AssertionError('unsafe source accepted: ' + failure)
                if failure == 'query':
                    assert len(requests) == count + 1
                    assert requests[-1]['path'].endswith('/')
                    assert not any(secret in row['path'] for row in requests)
            assert not any(row['source'] == 'beta' for row in requests)
            print('PASS same-host different-source and redirect isolation; 401/403 and userinfo/token/query redacted failures; valid query-before-SHA256 fragment rejected after page only, no artifact request', flush=True)
            mode['value'] = 'normal'
            both = copy.deepcopy(inputs)
            both['baseline']['config']['private_indexes']['beta'] = index.replace('/alpha/', '/beta/')
            both['baseline']['config']['private_bindings']['private-two'] = 'beta'
            isolated = RegistryBuilder(evidence / 'retained', '/usr/local/libexec/uv-pinned',
                private_snapshot=PrivateIndexSnapshot({'alpha': reference, 'beta': other_reference}, ca_file=certificate))
            isolated.build(both)
            assert any(row['source'] == 'beta' for row in requests)
            assert all(row['credential_matches_source'] for row in requests)
            print('PASS two private indexes sharing one HTTPS host receive only their own credentials', flush=True)
            for unbound in ('transitive', 'public-namesake'):
                missing = copy.deepcopy(inputs)
                if unbound == 'transitive':
                    del missing['baseline']['config']['private_bindings']['private-two']
                else:
                    missing['requirement']['declaration'] = 'public-wanted==1.0'
                    missing['ownership_effects']['normalized_name'] = 'public-wanted'
                    missing['baseline']['config']['private_bindings'] = {'public-wanted': 'alpha', 'private-core': 'alpha'}
                try:
                    builder.build(missing)
                except PreparationError as error:
                    assert 'explicit binding' in str(error) and 'no public fallback' in str(error)
                else:
                    raise AssertionError('public fallback accepted')
            print('PASS missing private transitive binding and available public namesake rejected before uv; no public fallback', flush=True)
            for source in ('git+ssh://example.invalid/repo', 'file:///etc/passwd',
                           'https://fixture:' + secret + '@localhost/pkg.whl',
                           'https://' + secret + '@localhost/pkg.whl',
                           'https://localhost/pkg.whl?token=' + secret):
                filename, contents = wheel('private-one', 'unsafe @ ' + source)
                packages[filename] = contents
                try:
                    builder.build(inputs)
                except PreparationError as error:
                    assert secret not in str(error) and 'direct sources are unsupported' in str(error)
                else:
                    raise AssertionError('unsupported transitive source accepted')
            packages.update([wheel('private-one', 'private-two==1.0')])
            print('PASS unsupported transitive SSH/local/direct credential sources rejected structurally with redacted actionable errors', flush=True)
            untrusted = RegistryBuilder(evidence / 'retained', '/usr/local/libexec/uv-pinned',
                                       private_snapshot=PrivateIndexSnapshot({'alpha': reference}))
            try:
                untrusted.build(inputs)
            except PreparationError as error:
                assert 'TLS' in str(error)
            else:
                raise AssertionError('untrusted TLS accepted')
            print('PASS TLS verification on by default, no trusted-host bypass', flush=True)
            for path in evidence.rglob('*'):
                if path.is_file():
                    assert secret.encode() not in path.read_bytes()
                    assert b'OTHER_DISPOSABLE_TOKEN' not in path.read_bytes()
                    assert wanted.encode() not in path.read_bytes()
            (evidence / 'requests.json').write_text(json.dumps(requests, indent=2))
            (evidence / 'uv-version.txt').write_bytes(subprocess.check_output(['/usr/local/libexec/uv-pinned', '--version']))
            print('PASS no raw/base64 credentials in retained sources, project/lock, attestation or evidence; credentials never supplied to uv', flush=True)
        finally:
            server.shutdown()
            thread.join()
    assert not reference.exists()
    print('PASS proof-only operator references removed after all synchronous workers exit; production committed references are not deleted', flush=True)


if __name__ == '__main__':
    main()
