"""Pinned uv 0.12.21 scoped private-index credentials through the operator root.

A disposable local HTTPS simple index with Basic auth stands in for a private
host; TLS verification stays ON via an explicit operator CA. Index credentials
live in an explicit owner-only operator root (one 0600 file per index label) and
are read unattended by the build. No secret is persisted in the retained bundle
or exposed in an error. Covers duplicate public/private names, declared index
ordering, transitive bindings, rotation/revocation, redirect rejection and a
frozen cold-cache install.
"""
from docassemble.base.managed_python import python_target
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import tarfile
import threading
import zipfile

from docassemble_executor.preparation import PreparationError
from docassemble_executor.private_index import load_operator_references
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained

TOKEN = 'DISPOSABLE_PRIVATE_TOKEN'
ROTATED = 'DISPOSABLE_ROTATED_TOKEN'
OTHER = 'DISPOSABLE_OTHER_TOKEN'


def wheel(name, version='1.0.0', dependency=None, extra_files=None):
    stem = name.replace('-', '_')
    info = stem + '-' + version + '.dist-info/'
    files = {stem + '/__init__.py': b'VALUE = 1\n',
             info + 'METADATA': ('Metadata-Version: 2.3\nName: ' + name + '\nVersion: ' + version + '\n'
                                 'Provides-Extra: feature\n' +
                                 ('Requires-Dist: ' + dependency + '\n' if dependency else '')).encode(),
             info + 'WHEEL': b'Wheel-Version: 1.0\nGenerator: proof\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    files.update(extra_files or {})
    records = [key + ',sha256=' + base64.urlsafe_b64encode(hashlib.sha256(value).digest()).decode().rstrip('=') + ',' + str(len(value))
               for key, value in files.items()]
    files[info + 'RECORD'] = ('\n'.join(records + [info + 'RECORD,,']) + '\n').encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for member, value in files.items():
            archive.writestr(member, value)
    return stem + '-' + version + '-py3-none-any.whl', output.getvalue()


def build_fixtures():
    """A private PEP 517 backend, static requirement and hook-discovered requirement.

    No public build tooling is needed. The backend must import its separately
    bound dynamic prerequisite, proving isolated build-env dependency resolution.
    """
    filename, payload = wheel('private-built', dependency='private-two==1.0.0')
    backend = ("import base64, json\nfrom pathlib import Path\n"
               "def get_requires_for_build_wheel(config_settings=None):\n"
               "    return json.loads(Path('build-requires.json').read_text())\n"
               "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
               "    import private_build_support\n"
               "    assert private_build_support.VALUE == 1\n"
               "    Path(wheel_directory, " + repr(filename) + ").write_bytes(base64.b64decode(" +
               repr(base64.b64encode(payload).decode()) + "))\n"
               "    return " + repr(filename) + "\n")
    return dict([wheel('private-build-backend', extra_files={'private_backend.py': backend.encode()}),
                 wheel('private-build-support')])


def sdist(dynamic='private-build-support==1.0.0'):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        files = {'pyproject.toml': ('[build-system]\nrequires = ["private-build-backend==1.0.0"]\n'
                                   'build-backend = "private_backend"\n'),
                 'build-requires.json': json.dumps([dynamic]),
                 'PKG-INFO': 'Metadata-Version: 2.3\nName: private-built\nVersion: 1.0.0\nRequires-Dist: private-two==1.0.0\n'}
        for name, value in files.items():
            data = value.encode()
            info = tarfile.TarInfo('private_built-1.0.0/' + name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return 'private_built-1.0.0.tar.gz', output.getvalue()


def basic(password):
    return 'Basic ' + base64.b64encode(('fixture:' + password).encode()).decode()


def build_inputs(core, requirement, bindings, indexes):
    return {'baseline': {'core': core, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                         'config': {'private_indexes': indexes, 'private_bindings': bindings},
                         'desired_inventory': core, 'runtime': {'kind': 'normal', 'inventory': core}},
            'contributions': [], 'requirement': {'declaration': requirement},
            'ownership_effects': {'normalized_name': requirement.split('[')[0].split('=')[0].split('>')[0].strip(),
                                  'owner_id': 'alice', 'existing': False}}


def main():
    uv = shutil.which('uv')
    assert uv is not None and subprocess.check_output([uv, '--version'], text=True).split()[:2] == ['uv', '0.12.21']
    with tempfile.TemporaryDirectory(prefix='private-index-credentials-') as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        certificate, key = root / 'ca.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                        '-out', str(certificate), '-days', '1', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
        packages = dict(wheel(name, version='1.0.0', dependency=dependency) for name, dependency in [
            ('private-one', 'private-two==1.0.0'), ('private-two', None), ('private-core', None),
            ('six', None)])
        packages['six-99.0-py3-none-any.whl'] = wheel('six', version='99.0')[1]
        packages.update(build_fixtures())
        packages.update(dict([sdist()]))
        expected = {'alpha': TOKEN, 'beta': OTHER}
        requests = []
        mode = {'value': 'normal'}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                auth = self.headers.get('Authorization')
                label = 'beta' if self.path.startswith('/beta/') else 'alpha'
                requests.append({'path': self.path, 'label': label,
                                 'credential_matches_label': auth == basic(expected[label])})
                assert not (label == 'beta' and auth == basic(expected['alpha'])), 'cross-index credential exposure'
                if mode['value'] in ('401', '403'):
                    self.send_error(int(mode['value']))
                    return
                if auth != basic(expected[label]):
                    self.send_error(401)
                    return
                if mode['value'] == 'redirect' or (mode['value'] == 'build-redirect' and '/private-build-backend/' in self.path):
                    self.send_response(302)
                    self.send_header('Location', '/beta/simple/private-one/')
                    self.end_headers()
                    return
                filename = self.path.rsplit('/', 1)[-1]
                if filename in packages:
                    data = packages[filename]
                else:
                    name = self.path.rstrip('/').rsplit('/', 1)[-1]
                    filename = name.replace('-', '_') + '-1.0.0-py3-none-any.whl'
                    if name == 'private-built':
                        filename = 'private_built-1.0.0.tar.gz'
                    if name == 'six':
                        filename = 'six-99.0-py3-none-any.whl'
                    if filename not in packages:
                        self.send_error(404)
                        return
                    link = filename + '#sha256=' + hashlib.sha256(packages[filename]).hexdigest()
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
        alpha = 'https://localhost:' + str(server.server_port) + '/alpha/simple/'
        beta = alpha.replace('/alpha/', '/beta/')
        indexes = {'alpha': alpha, 'beta': beta}
        credential_root = root / 'credentials'
        credential_root.mkdir(mode=0o700)

        def reference(label, password):
            path = credential_root / label
            path.write_text(json.dumps({'index_url': indexes[label], 'username': 'fixture', 'password': password}))
            path.chmod(0o600)

        reference('alpha', TOKEN)
        reference('beta', OTHER)
        assert set(load_operator_references(credential_root)) == {'alpha', 'beta'}
        core = {'private-core': {'version': '1.0.0', 'source_digest': hashlib.sha256(packages['private_core-1.0.0-py3-none-any.whl']).hexdigest()}}
        bindings = {'private-one': 'alpha', 'private-two': 'alpha', 'private-core': 'alpha', 'six': 'beta'}
        inputs = build_inputs(core, 'private-one[feature]>=1; python_version >= "3.12"', bindings, indexes)
        builder = RegistryBuilder(root / 'bundles', uv, index_credential_root=credential_root, index_ca_file=certificate)

        def refuse(value, needle):
            try:
                builder.build(value)
            except PreparationError as error:
                assert needle in str(error), str(error)
                assert not any(secret in str(error) for secret in
                               (TOKEN, ROTATED, OTHER, basic(TOKEN), basic(ROTATED), basic(OTHER)))
                return
            raise AssertionError('unsafe private index use accepted: ' + needle)

        try:
            result = builder.build(inputs)
            inventory = result['bundle']['inventory']
            assert set(inventory) == {'private-one', 'private-two', 'private-core'}
            assert inventory['private-core'] == core['private-core']
            assert result['warnings'] == ['private-source:private-core:alpha', 'private-source:private-one:alpha',
                                          'private-source:private-two:alpha', 'private-source:six:beta']
            bundle = root / 'bundles' / result['bundle']['digest']
            verify_retained(bundle, result['bundle']['digest'])
            provenance = json.loads((bundle / 'private-sources.json').read_text())
            assert [row['name'] for row in provenance] == ['private-core', 'private-one', 'private-two', 'six']
            assert provenance[0]['index'] == 'alpha' and provenance[-1]['index'] == 'beta'
            for path in bundle.rglob('*'):
                if path.is_file():
                    for secret in (TOKEN, ROTATED, OTHER, basic(TOKEN), basic(OTHER)):
                        assert secret.encode() not in path.read_bytes()
            print('PASS pinned uv 0.12.21 operator-root credentials, declared ordering, transitive binding, secret-free retained bundle', flush=True)

            # Duplicate public/private name: the private namesake resolves offline, never public.
            duplicate = build_inputs(core, 'six==99.0', {'six': 'beta', 'private-core': 'alpha'}, indexes)
            private_six = builder.build(duplicate)
            assert private_six['bundle']['inventory']['six']['version'] == '99.0'
            unbound = build_inputs(core, 'six==99.0', {'private-core': 'alpha'}, indexes)
            refuse(unbound, 'explicit private index binding for this requirement; missing six')
            print('PASS duplicate public/private name resolves the bound private namesake; unbinding refuses before uv without public fallback', flush=True)

            # Transitive binding: a declared parent does not bind its closure.
            transitive = build_inputs(core, 'private-one>=1', {'private-one': 'alpha', 'private-core': 'alpha'}, indexes)
            refuse(transitive, 'private transitive dependency private-two of wheel private-one lacks an explicit binding')
            print('PASS missing transitive private binding names the dependency and parent wheel before uv', flush=True)

            # Rotation: the retired token stops working; a fresh token in the same label file supersedes.
            expected['alpha'] = ROTATED
            refuse(inputs, '401/403')
            reference('alpha', ROTATED)
            rotated = builder.build(inputs)
            assert rotated['bundle']['inventory'] == result['bundle']['inventory']
            assert rotated['warnings'] == result['warnings']
            expected['alpha'] = TOKEN
            reference('alpha', TOKEN)
            print('PASS index credential rotation supersedes in place; retired token fails closed', flush=True)

            # Revocation: deleting the label file fails the next fetch closed.
            (credential_root / 'alpha').unlink()
            refuse(inputs, 'private index requires an operator credential reference for each selected index')
            reference('alpha', TOKEN)
            print('PASS index credential revocation (deleted reference file) fails the next fetch closed', flush=True)

            # Redirect rejection and TLS verification stay on.
            mode['value'] = 'redirect'
            refuse(inputs, 'no public fallback')
            mode['value'] = 'normal'
            untrusted = RegistryBuilder(root / 'bundles', uv, index_credential_root=credential_root)
            try:
                untrusted.build(inputs)
            except PreparationError as error:
                assert 'TLS' in str(error) and TOKEN not in str(error)
            else:
                raise AssertionError('untrusted TLS accepted')
            print('PASS redirect rejected and TLS verification on by default; no trusted-host bypass', flush=True)

            # An actual private source build, not a wheel-only --no-build proof.
            build_bindings = {'private-built': 'alpha', 'private-two': 'alpha', 'private-core': 'alpha',
                              'private-build-backend': 'beta', 'private-build-support': 'beta'}
            build_value = build_inputs(core, 'private-built==1.0.0', build_bindings, indexes)
            start = len(requests)
            built = builder.build(build_value)  # every build creates an empty cache and isolated venv
            assert set(built['bundle']['inventory']) == {'private-built', 'private-two', 'private-core'}
            build_requests = requests[start:]
            assert any(row['path'].endswith('.tar.gz') for row in build_requests)
            for dependency in ('private-build-backend', 'private-build-support'):
                assert any('/' + dependency + '/' in row['path'] and row['label'] == 'beta'
                           and row['credential_matches_label'] for row in build_requests)
            build_bundle = root / 'bundles' / built['bundle']['digest']
            verify_retained(build_bundle, built['bundle']['digest'])
            retained_source = next((build_bundle / 'sources').glob('*.tar.gz'))
            assert retained_source.read_bytes() == packages['private_built-1.0.0.tar.gz']
            source_row = next(row for row in json.loads((build_bundle / 'private-sources.json').read_text())
                              if row['name'] == 'private-built')
            assert source_row['sha256'] == hashlib.sha256(retained_source.read_bytes()).hexdigest()
            transformed = build_bundle / source_row['build_input']['path']
            assert source_row['build_input']['sha256'] == hashlib.sha256(transformed.read_bytes()).hexdigest()
            assert transformed.read_bytes() != retained_source.read_bytes()
            print('PASS private sdist, authenticated static and hook-discovered build dependencies, isolated cold-cache build', flush=True)

            missing = build_inputs(core, 'private-built==1.0.0',
                                   {k: v for k, v in build_bindings.items() if k != 'private-build-backend'}, indexes)
            refuse(missing, 'private build dependency private-build-backend of sdist private-built lacks an explicit binding')
            # A hook-discovered name without a binding cannot reach any online index.
            missing_dynamic = build_inputs(core, 'private-built==1.0.0',
                {k: v for k, v in build_bindings.items() if k != 'private-build-support'}, indexes)
            refuse(missing_dynamic, 'registry preparation command failed')
            original_sdist = packages['private_built-1.0.0.tar.gz']
            support_name = 'private_build_support-1.0.0-py3-none-any.whl'
            local_support = root / support_name
            local_support.write_bytes(packages[support_name])
            for dynamic in ('unknown-build-dependency==1.0.0',
                            'private-build-support @ ' + local_support.as_uri(),
                            'private-build-support @ ' + beta + 'private-build-support/' + support_name,
                            'private-build-support @ https://' + TOKEN + '@localhost/source.whl',
                            str(local_support)):
                packages.update(dict([sdist(dynamic)]))
                refuse(build_value, 'registry preparation command failed')
            packages['private_built-1.0.0.tar.gz'] = original_sdist
            assert not any('unknown-build-dependency' in row['path'] for row in requests)
            print('PASS static/dynamic missing bindings, unknown names and hook-returned file/direct/secret URL requirements fail closed', flush=True)

            # Rotation/revocation on the index serving the build prerequisites.
            expected['beta'] = ROTATED
            refuse(build_value, '401/403')
            reference('beta', ROTATED)
            assert builder.build(build_value)['bundle']['inventory'] == built['bundle']['inventory']
            (credential_root / 'beta').unlink()
            refuse(build_value, 'private index requires an operator credential reference for each selected index')
            expected['beta'] = OTHER
            reference('beta', OTHER)
            mode['value'] = 'build-redirect'
            refuse(build_value, 'no public fallback')
            mode['value'] = 'normal'
            print('PASS build-index credential rotation/revocation and build-backend redirect rejection', flush=True)

            # Frozen application needs neither the source host nor any build backend.
            server.shutdown()
            builder._run(['sync', '--frozen', '--no-build', '--offline', '--no-config'],
                         build_bundle, root / 'build-apply-cache', root / 'build-apply-venv')
            assert subprocess.check_output([str(root / 'build-apply-venv' / 'bin/python'), '-I', '-c',
                'import private_built; print(private_built.VALUE)'], text=True).strip() == '1'
            verify_retained(build_bundle, built['bundle']['digest'])
            for value in (built, build_value, requests):
                text = json.dumps(value, sort_keys=True)
                assert not any(secret in text for secret in (TOKEN, ROTATED, OTHER, basic(TOKEN), basic(ROTATED), basic(OTHER)))
            for path in build_bundle.rglob('*'):
                if path.is_file():
                    assert not any(secret.encode() in path.read_bytes()
                                   for secret in (TOKEN, ROTATED, OTHER, basic(TOKEN), basic(ROTATED), basic(OTHER)))
            print('PASS private source-build secret-free results/provenance/locks/bundle and host-unavailable frozen offline cold-cache application', flush=True)

            # Frozen cold-cache application of the exact retained private namesake bytes.
            six_bundle = root / 'bundles' / private_six['bundle']['digest']
            builder._run(['sync', '--frozen', '--no-build', '--offline', '--no-config'],
                         six_bundle, root / 'relocated-cache', root / 'relocated-venv')
            check = subprocess.check_output([str(root / 'relocated-venv' / 'bin/python'), '-I', '-c',
                'import six; print(six.__name__)'], text=True).strip()
            assert check == 'six'
            verify_retained(six_bundle, private_six['bundle']['digest'])
            print('PASS frozen offline cold-cache install from relocated retained bytes', flush=True)
        finally:
            server.shutdown()
            thread.join()
        print(json.dumps({'uv': '0.12.21', 'operator_credential_root': True, 'duplicate_public_private_name': True,
                          'declared_ordering': True, 'transitive_binding': True, 'rotation_supersedes': True,
                          'revocation_fails_closed': True, 'redirect_rejected': True, 'tls_verified': True,
                          'cold_offline_frozen_apply': True, 'bundle_secret_free': True, 'private_sdist_build': True, 'static_and_dynamic_build_binding': True,
                          'scope': 'disposable-local-https-fixture-not-live-private-host'}, sort_keys=True))


if __name__ == '__main__':
    main()
