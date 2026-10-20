"""Pinned uv 0.12.21 direct wheel/archive URL proof.

A disposable local HTTPS host (explicit operator CA, TLS verification ON) stands
in for an upstream artifact host. Proves: exact fetched bytes recorded in the
content-addressed store, install/build with pinned uv, frozen cold-cache
reinstall of the recorded bytes, unrelated-solve reuse without refetch, changed
bytes at a recorded URL failing closed, explicit Update preparing a new revision,
upstream unavailability reported without substitution, and secret-free retention.
External availability limits are recorded, not guaranteed.
"""
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
import threading
import zipfile

from docassemble.base.managed_direct import parse_direct_requirement
from docassemble_executor.preparation import PreparationError
from docassemble_executor.direct_sources import download_source, resolve_direct_sources
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained

TOKEN = 'DISPOSABLE_DIRECT_TOKEN'


def artifact_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state['requests'] += 1
            if state.get('authorization'):
                if self.headers.get('Authorization') != state['authorization']:
                    self.send_error(401)
                    return
                state['authenticated_requests'] += 1
            if state['status'] != 200:
                self.send_error(state['status'])
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.end_headers()
            self.wfile.write(state['bytes'])
    return Handler


def wheel(name, version='1.0'):
    stem = name.replace('-', '_')
    info = stem + '-' + version + '.dist-info/'
    files = {stem + '/__init__.py': b'VALUE = 1\n',
             info + 'METADATA': ('Metadata-Version: 2.3\nName: ' + name + '\nVersion: ' + version + '\n').encode(),
             info + 'WHEEL': b'Wheel-Version: 1.0\nGenerator: proof\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    records = [key + ',sha256=' + base64.urlsafe_b64encode(hashlib.sha256(value).digest()).decode().rstrip('=') + ',' + str(len(value))
               for key, value in files.items()]
    files[info + 'RECORD'] = ('\n'.join(records + [info + 'RECORD,,']) + '\n').encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for member, value in files.items():
            archive.writestr(member, value)
    return stem + '-' + version + '-py3-none-any.whl', output.getvalue()


def baseline(**overrides):
    value = {'core': {}, 'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
             'config': {'index_url': 'https://pypi.org/simple'}, 'desired_inventory': {},
             'runtime': {'kind': 'normal', 'inventory': {}}}
    value.update(overrides)
    return value


def main(archive=False):
    uv = shutil.which('uv')
    assert uv is not None and subprocess.check_output([uv, '--version'], text=True).split()[:2] == ['uv', '0.12.21']
    with tempfile.TemporaryDirectory(prefix='direct-url-proof-') as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        certificate, key = root / 'ca.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                        '-out', str(certificate), '-days', '1', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
        if archive:
            from zip_fixture import distribution
            filename = 'ZipExample-1.0.zip'
            first_bytes = distribution(root / filename, name='ZipExample', wheel=False).read_bytes()
            second_bytes = distribution(root / 'ZipExample-2.0.zip', name='ZipExample', version='2.0', wheel=False).read_bytes()
        else:
            filename, first_bytes = wheel('ZipExample', '1.0')
            _, second_bytes = wheel('ZipExample', '2.0')
        state = {'bytes': first_bytes, 'status': 200, 'requests': 0}

        server = ThreadingHTTPServer(('127.0.0.1', 0), artifact_handler(state))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = 'https://localhost:' + str(server.server_port) + '/files/' + filename
        try:
            store, bundles = root / 'artifacts', root / 'bundles'
            store.mkdir(mode=0o700)
            builder = RegistryBuilder(bundles, uv, artifact_root=store, direct_ca_file=certificate)
            requirement, source, _ = parse_direct_requirement('ZipExample @ ' + base_url)
            direct = {'baseline': baseline(), 'contributions': [], 'requirement': requirement.__dict__,
                      'direct_source': source, 'ownership_effects': {}}
            result = builder.build(direct)
            bundle = bundles / result['bundle']['digest']
            verify_retained(bundle, result['bundle']['digest'])
            recorded = result['direct_sources']['zipexample']
            assert recorded['digest'] == hashlib.sha256(first_bytes).hexdigest()
            assert recorded['version'] == '1.0' and recorded['url'] == base_url
            assert result['bundle']['inventory']['zipexample'] == {
                'version': '1.0', 'source_digest': recorded['digest']}
            assert (bundle / 'artifacts' / recorded['filename']).read_bytes() == first_bytes
            assert (store / recorded['digest']).read_bytes() == first_bytes
            print('PASS direct wheel fetched, recorded and solved with pinned uv', flush=True)

            # Frozen + cold-cache reinstall of the recorded bytes: the builder
            # already wipes its cache/venv and syncs --frozen --offline. Reuse
            # must not touch the (now failing) upstream.
            state['status'] = 500
            frozen = {'baseline': baseline(direct_sources={'zipexample': recorded},
                                           desired_inventory=result['bundle']['inventory'],
                                           runtime={'kind': 'normal', 'inventory': result['bundle']['inventory']}),
                      'contributions': [{'id': 'direct', 'declaration': requirement.declaration}],
                      'requirement': {'declaration': 'zipexample', 'normalized_name': 'zipexample'},
                      'ownership_effects': {}}
            before_requests = state['requests']
            again = builder.build(frozen)
            assert again['bundle']['inventory'] == result['bundle']['inventory']
            assert again['direct_sources'] == {'zipexample': recorded}
            assert state['requests'] == before_requests
            verify_retained(bundles / again['bundle']['digest'], again['bundle']['digest'])
            print('PASS frozen reuse of recorded bytes without upstream access', flush=True)

            # Changed bytes at the recorded URL fail closed for a non-Update request.
            state['status'], state['bytes'] = 200, second_bytes
            changed = {'baseline': baseline(direct_sources={'zipexample': recorded}),
                       'contributions': [], 'requirement': requirement.__dict__,
                       'direct_source': source, 'ownership_effects': {}}
            try:
                builder.build(changed)
            except PreparationError as error:
                assert str(error) == 'direct URL content changed; explicit Update required', str(error)
            else:
                raise AssertionError('changed bytes at a recorded URL were accepted')
            print('PASS changed bytes at the recorded URL rejected without an Update', flush=True)

            # Explicit Update prepares the new bytes as a new revision.
            update = {'baseline': baseline(direct_sources={'zipexample': recorded},
                                            desired_inventory=result['bundle']['inventory'],
                                            publication={'result': result}),
                      'contributions': [{'id': 'direct', 'declaration': requirement.declaration}],
                      'requirement': {'declaration': 'zipexample', 'normalized_name': 'zipexample'},
                      'change_operation': 'update', 'ownership_effects': {}}
            advanced = builder.build(update)
            new = advanced['direct_sources']['zipexample']
            assert new['version'] == '2.0' and new['digest'] == hashlib.sha256(second_bytes).hexdigest()
            assert advanced['bundle']['inventory']['zipexample'] == {'version': '2.0', 'source_digest': new['digest']}
            verify_retained(bundles / advanced['bundle']['digest'], advanced['bundle']['digest'])
            print('PASS explicit Update prepared and solved the new revision', flush=True)

            # Expiry/unavailability is reported, never substituted.
            state['status'] = 404
            try:
                builder.build({'baseline': baseline(), 'contributions': [], 'requirement': requirement.__dict__,
                               'direct_source': source, 'ownership_effects': {}})
            except PreparationError as error:
                assert str(error) == ('direct URL artifact unavailable; submit a new URL or supported '
                                      'source through normal preparation and approval'), str(error)
            else:
                raise AssertionError('unavailable upstream was substituted')
            print('PASS expired/unavailable direct URL reported, never substituted', flush=True)

            # Supplied credentials are used but never retained in the bundle.
            state.update(status=200, bytes=first_bytes, authenticated_requests=0,
                         authorization='Basic ' + base64.b64encode(('user:' + TOKEN).encode()).decode())
            for rejected_url in (base_url, base_url.replace('https://', 'https://user:WRONG@')):
                try:
                    download_source(rejected_url, ca_file=certificate)
                except PreparationError:
                    pass
                else:
                    raise AssertionError('credential fixture admitted anonymous or wrong credentials')
            assert state['authenticated_requests'] == 0
            credentialed, credentialed_source, _ = parse_direct_requirement(
                'ZipExample @ https://user:' + TOKEN + '@localhost:' + str(server.server_port) + '/files/' + filename)
            secret_inputs = {'baseline': baseline(), 'contributions': [], 'requirement': credentialed.__dict__,
                             'direct_source': credentialed_source, 'ownership_effects': {}}
            secret = builder.build(secret_inputs)
            assert state['authenticated_requests'] > 0, 'builder did not authenticate its retrieval'
            secret_bundle = bundles / secret['bundle']['digest']
            for path in secret_bundle.rglob('*'):
                if path.is_file():
                    assert TOKEN.encode() not in path.read_bytes(), path
            before_authenticated = state['authenticated_requests']
            sources, _ = resolve_direct_sources(secret_inputs, store, ca_file=certificate)
            assert state['authenticated_requests'] > before_authenticated
            assert TOKEN in sources['zipexample']['url'] and TOKEN not in credentialed.declaration
            print('PASS supplied credential used for retrieval but absent from the retained bundle', flush=True)
        finally:
            server.shutdown()
            server.server_close()

        print(json.dumps({'uv': '0.12.21', 'formats': ['zip-sdist' if archive else 'wheel'], 'recorded_hash': True,
              'frozen_reuse_without_refetch': True, 'changed_bytes_rejected': True,
              'explicit_update_new_revision': True, 'unavailable_reported': True,
              'credential_used_not_retained': True,
              'external_availability_limits': 'upstream bytes are recorded at prepare time; '
              'a later unrelated solve never revalidates the upstream'}, sort_keys=True))
        print('PASS direct URL wheel install, content mutation, expiry, approved replacement and frozen verification')


if __name__ == '__main__':
    main()
    main(archive=True)
