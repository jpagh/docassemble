"""Pinned private HTTPS Git: scoped token -> cold-cache build -> frozen offline apply.

Disposable schema-free proof. A local HTTPS git fixture with a self-signed CA
stands in for a private host; TLS verification stays ON via an explicit CA file.
No real credential is used and no secret is persisted in the retained bundle.
"""
from __future__ import annotations

from docassemble.base.managed_python import python_target

import base64
import http.server
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading

from docassemble.base.managed_git import git_credential_material, parse_git_requirement
from docassemble.base.managed_preparation import PreparationError
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained
from frozen_apply_diagnostics import frozen_apply_failure

TOKEN = 'DISPOSABLE_PRIVATE_GIT_TOKEN'
ROTATED = 'DISPOSABLE_ROTATED_GIT_TOKEN'


class _Token:
    def __init__(self, value):
        self.value = value


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'private-git-proof'
    token: _Token = None
    project_root: Path = None

    def log_message(self, *args):
        pass

    def _unauthorized(self):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="private"')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _serve(self):
        expected = 'Basic ' + base64.b64encode((self.token.value + ':').encode()).decode()
        if self.headers.get('Authorization') != expected:
            self._unauthorized()
            return
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        path, _, query = self.path.partition('?')
        env = dict(os.environ, GIT_PROJECT_ROOT=str(self.project_root), GIT_HTTP_EXPORT_ALL='1',
                   REQUEST_METHOD=self.command, PATH_INFO=path, QUERY_STRING=query,
                   CONTENT_TYPE=self.headers.get('Content-Type', ''), CONTENT_LENGTH=str(length),
                   REMOTE_USER='git', REMOTE_ADDR='127.0.0.1')
        result = subprocess.run(['git', 'http-backend'], input=body, env=env, capture_output=True, timeout=60)
        head, _, payload = result.stdout.partition(b'\r\n\r\n')
        status, headers = 200, []
        for line in head.split(b'\r\n'):
            name, _, value = line.partition(b': ')
            if name.lower() == b'status':
                status = int(value.split()[0])
            elif name:
                headers.append((name.decode(), value.decode()))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._serve()

    def do_POST(self):
        self._serve()


class GitFixture:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.repo_root = root / 'repos'
        self.ca = root / 'ca.pem'
        self.token = _Token(TOKEN)
        self.httpd = None

    def certificates(self):
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout',
                        str(self.root / 'ca.key'), '-out', str(self.ca), '-days', '2', '-subj', '/CN=private-ca'],
                       check=True, capture_output=True)
        subprocess.run(['openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout',
                        str(self.root / 'server.key'), '-out', str(self.root / 'server.csr'),
                        '-subj', '/CN=localhost'], check=True, capture_output=True)
        (self.root / 'ext.cnf').write_text('subjectAltName = DNS:localhost, IP:127.0.0.1\n')
        subprocess.run(['openssl', 'x509', '-req', '-in', str(self.root / 'server.csr'),
                        '-CA', str(self.ca), '-CAkey', str(self.root / 'ca.key'), '-CAcreateserial',
                        '-out', str(self.root / 'server.pem'), '-days', '2', '-extfile', str(self.root / 'ext.cnf')],
                       check=True, capture_output=True)

    def repository(self, name: str, package: str, version: str = '1.0.0'):
        repo = self.repo_root / 'private' / name
        repo.mkdir(parents=True)
        subprocess.run(['git', 'init', '--quiet', '--initial-branch=main', str(repo)], check=True)
        subdirectory = repo / 'pkg'
        (subdirectory / package).mkdir(parents=True)
        (subdirectory / package / '__init__.py').write_text('VALUE = 42\n')
        (subdirectory / 'pyproject.toml').write_text(
            '[build-system]\nrequires = ["setuptools>=68", "wheel"]\nbuild-backend = "setuptools.build_meta"\n\n'
            '[project]\nname = "' + package.replace('_', '-') + '"\nversion = "' + version + '"\n'
            'requires-python = ">=3.12"\n')
        env = dict(os.environ, GIT_AUTHOR_NAME='fixture', GIT_AUTHOR_EMAIL='f@example.invalid',
                   GIT_COMMITTER_NAME='fixture', GIT_COMMITTER_EMAIL='f@example.invalid')
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True, env=env)
        subprocess.run(['git', '-C', str(repo), 'commit', '--quiet', '-m', 'fixture'], check=True, env=env)
        return repo

    def start(self) -> str:
        handler = type('BoundHandler', (_Handler,), {'token': self.token, 'project_root': self.repo_root})
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.root / 'server.pem', self.root / 'server.key')
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return 'https://localhost:' + str(self.httpd.server_address[1])

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


def inputs(declaration: str, authorizations: dict | None = None) -> dict:
    requirement, source, _ = parse_git_requirement(declaration)
    value = {'requirement': requirement.__dict__, 'git_source': source, 'contributions': [], 'ownership_effects': {},
             'baseline': {'core': {}, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                          'config': {'index_url': 'https://pypi.org/simple'},
                          'desired_inventory': {}, 'runtime': {'kind': 'normal', 'inventory': {}}}}
    if authorizations:
        value['git_authorizations'] = authorizations
    return value


def build(builder: RegistryBuilder, declaration: str, repository: str, token: str) -> dict:
    payload = git_credential_material(repository, token)
    return builder.build(inputs(declaration, {'private-sample': payload}))


def main():
    uv = shutil.which('uv')
    assert uv is not None and subprocess.check_output([uv, '--version'], text=True).split()[:2] == ['uv', '0.12.21']
    with tempfile.TemporaryDirectory(prefix='private-git-proof-') as directory:
        root = Path(directory)
        fixture = GitFixture(root / 'fixture')
        fixture.certificates()
        fixture.repository('repo.git', 'private_sample')
        fixture.repository('other.git', 'other_sample')
        base = fixture.start()
        repository = base + '/private/repo.git'
        declaration = 'private-sample @ git+https://' + TOKEN + '@' + repository.split('://', 1)[1] + '@main#subdirectory=pkg'
        builder = RegistryBuilder(root / 'bundles', uv, git_ca_file=fixture.ca)
        result = build(builder, declaration, repository, TOKEN)
        source = result['git_sources']['private-sample']
        assert source['repository'] == repository and source['subdirectory'] == 'pkg' and source['ref_kind'] == 'branch'
        assert source['resolved_sha'] == subprocess.check_output(
            ['git', '-C', str(fixture.repo_root / 'private' / 'repo.git'), 'rev-parse', 'HEAD'], text=True).strip()
        inventory = result['bundle']['inventory']
        assert list(inventory) == ['private-sample'] and inventory['private-sample']['version'] == '1.0.0'
        assert len(inventory['private-sample']['source_digest']) == 64
        assert result['desired_diff'] == result['runtime_diff'] == {
            'private-sample': {'before': None, 'after': inventory['private-sample']}}
        bundle = root / 'bundles' / result['bundle']['digest']
        verify_retained(bundle, result['bundle']['digest'])
        # No secret may survive in the retained, content-addressed bundle.
        for path in bundle.rglob('*'):
            if path.is_file():
                assert TOKEN.encode() not in path.read_bytes() and base64.b64encode((TOKEN + ':').encode()) not in path.read_bytes()
        runtime = root / 'runtime'
        env = {'PATH': os.environ['PATH'], 'HOME': str(root), 'UV_PROJECT_ENVIRONMENT': str(runtime),
               'UV_CACHE_DIR': str(root / 'empty-cache'), 'UV_PYTHON_DOWNLOADS': 'never'}
        applied = subprocess.run([uv, 'sync', '--frozen', '--no-build', '--offline', '--no-config', '--python', sys.executable],
                                 cwd=bundle, env=env, text=True, capture_output=True)
        assert applied.returncode == 0, frozen_apply_failure(applied, 'cold offline apply failed',
                                                           credentials=(TOKEN, ROTATED))
        check = subprocess.check_output([str(runtime / 'bin/python'), '-I', '-c',
            'import private_sample; print(private_sample.VALUE)'], text=True).strip()
        assert check == '42'
        # Unrelated repository: the token is never reused across the same host.
        other = inputs('other-sample @ git+https://' + TOKEN + '@' + base.split('://', 1)[1] + '/private/other.git@main#subdirectory=pkg',
                       {'other-sample': git_credential_material(repository, TOKEN)})
        try:
            builder.build(other)
            raise AssertionError('out-of-scope credential accepted')
        except PreparationError as error:
            assert str(error) == 'Git credential scope mismatch'
        # Wrong token and unauthenticated access both fail closed without an echo.
        for authorization in (None, {'private-sample': git_credential_material(repository, 'WRONG')}):
            try:
                builder.build(inputs(declaration, authorization))
                raise AssertionError('private source accepted without a valid credential')
            except PreparationError as error:
                assert TOKEN not in str(error) and 'private_sample' not in str(error)
        # Rotation: the retired token stops working; a fresh token keeps access.
        fixture.token.value = ROTATED
        try:
            build(builder, declaration, repository, TOKEN)
            raise AssertionError('rotated credential still accepted')
        except PreparationError:
            pass
        rotated = build(builder, declaration, repository, ROTATED)
        assert rotated['git_sources']['private-sample'] == source
        fixture.stop()
        print(json.dumps({'uv': '0.12.21', 'repository': repository, 'resolved_sha': source['resolved_sha'],
            'bundle': result['bundle']['digest'], 'exact_source_identity': True, 'scoped_token_reuse_refused': True,
            'wrong_or_missing_token_refused': True, 'rotation_supersedes': True,
            'cold_offline_frozen_apply': True, 'runtime_value': check, 'bundle_secret_free': True,
            'scope': 'disposable-local-https-fixture-not-live-private-host'}, sort_keys=True))
        print('PASS: private HTTPS Git scoped token -> cold build -> frozen offline apply')


if __name__ == '__main__':
    sys.exit(main())
