"""Bounded independent public-registry preparation, never live application.

Image-provisioned Python / uv 0.12.21 public PyPI or opt-in offline private distribution
snapshots only. Preparation container and credential promotion are separate.
"""
from __future__ import annotations

from docassemble.base.managed_python import python_target, python_requires

import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .private_index import PrivateIndexSnapshot
    from .image_baseline import ImageRegistryWheel

from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

from .bundles import sha256_file, verify_python_target
from .inventory import inspect_environment, verify_environment, wheel_payload_material
from .preparation import Preparation, PreparationError, digest, parse_registry_requirement
from docassemble.base.managed_git import parse_supported_requirement, registry_declaration
from docassemble.base.managed_direct import direct_declarations
from docassemble.base.managed_release import (BASE, DEMO, WEBAPP, coherent_core, core_requirements,
                                              parse_release_selection, reject_downgrade, select_release,
                                              upgrade_risk_disclosure)
from .git_sources import resolve_sources, build_git_wheels, git_source_digest
from .direct_sources import resolve_direct_sources
from .release_catalog import default_release_catalog

UV_VERSION = '0.12.21'
INDEX = 'https://pypi.org/simple'


class _RefuseRegistryRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PreparationError('unsupported artifact redirect')


def _open_registry_artifact(url: str) -> Any:
    # Refuse at the redirect boundary, before any off-lock request is issued.
    return urllib.request.build_opener(_RefuseRegistryRedirects()).open(url, timeout=60)


def validate_public_policy(inputs: dict[str, Any]) -> None:
    """Reject unsupported sources before writing container inputs or invoking uv."""
    baseline = inputs['baseline']
    config = baseline['config']
    if inputs.get('change_operation') == 'config':
        from docassemble.base.managed_config import config_import_policy
        config = config_import_policy(inputs['config_import']['snapshot'], baseline['config'])
        if 'private_indexes' in config or 'private_bindings' in config:
            raise PreparationError('private indexes require operator credential references')
    if (baseline['target'] != python_target() or baseline['tool'] != {'uv': UV_VERSION}
            or config != {'index_url': INDEX}):
        raise PreparationError('unsupported builder target, tool or index policy')
    declarations = [row['declaration'] for row in inputs['contributions']]
    if inputs.get('change_operation') not in ('config', 'repair', 'upgrade', 'adoption', 'migration'):
        declarations.append(inputs['requirement']['declaration'])
    for declaration in declarations:
        parse_supported_requirement(declaration)


def verify_retained(directory: Path, expected: str) -> None:
    """Verify every retained byte, including source, wheel and solve provenance."""
    manifest = json.loads((directory / 'manifest.json').read_text())
    if digest(manifest) != expected:
        raise PreparationError('retained manifest mismatch')
    files = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if files != set(manifest) | {'manifest.json'} or any(p.is_symlink() for p in directory.rglob('*')):
        raise PreparationError('retained file set mismatch')
    if any(sha256_file(directory / name) != value for name, value in manifest.items()):
        raise PreparationError('retained bytes mismatch')


def publish_retained(staging: Path, retained: Path, expected: str) -> None:
    """Reuse a racing identical publisher, but never mask unrelated IO errors."""
    try:
        os.rename(staging, retained)
    except OSError as error:
        if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
            raise
        verify_retained(retained, expected)
        shutil.rmtree(staging)


def validate_artifact_requirements(requirements: list[str], artifacts: dict[str, tuple[dict[str, str], Path]],
                                   installed: dict[str, str] | None = None) -> None:
    """uv 0.12.21 ignores root version bounds for path sources; never accept that.

    Before solving use authoritative archive metadata. Afterwards use the actual
    installed METADATA inventory, not the lock's asserted version. Extras and
    declaration strings remain intact for uv's dependency/extra resolution.
    """
    from packaging.requirements import Requirement
    for name, (identity, _) in artifacts.items():
        version = identity['version'] if installed is None else installed.get(name)
        if installed is not None and version is not None and version != identity['version']:
            raise PreparationError('retained artifact metadata does not satisfy declarations')
        for declaration in requirements:
            requirement = Requirement(declaration)
            if canonicalize_name(requirement.name) != name or (
                    requirement.marker is not None and not requirement.marker.evaluate({'extra': ''})):
                continue
            if version is None or not requirement.specifier.contains(version, prereleases=True):
                raise PreparationError('retained artifact metadata does not satisfy declarations')


def retained_artifacts(inputs: dict[str, Any], root: Path | None) -> dict[str, tuple[dict[str, str], Path]]:
    from docassemble.base.managed_artifacts import verify_artifact, distribution_identity, open_regular
    references = [row['artifact_ref'] for row in inputs['contributions'] if row.get('artifact_ref') and
                  row['id'] != inputs.get('remove_contribution_id') and
                  row['id'] not in inputs.get('replace_artifact_contributions', [])]
    if inputs.get('artifact'):
        references.append(inputs['artifact']['digest'])
    if references and root is None:
        raise PreparationError('explicit artifact store configuration required')
    artifacts: dict[str, tuple[dict[str, str], Path]] = {}
    for reference in references:
        path = verify_artifact(root, reference)
        with open_regular(path, owner=path.parent.stat().st_uid) as handle:
            identity = dict(distribution_identity(handle), digest=reference)
        name = identity['normalized_name']
        if name in inputs['baseline']['core'] or (name in artifacts and artifacts[name][0] != identity):
            raise PreparationError('conflicting distribution artifact sources')
        if inputs.get('artifact', {}).get('digest') == reference and identity != inputs['artifact']:
            raise PreparationError('retained distribution identity mismatch')
        for row in inputs['contributions']:
            if row.get('artifact_ref') == reference and parse_registry_requirement(row['declaration']).normalized_name != name:
                raise PreparationError('retained distribution identity mismatch')
        artifacts[name] = identity, path
    return artifacts


class RegistryBuilder:
    def __init__(self, store: Path, uv_binary: str = 'uv', image_baseline: Path | None = None,
                 private_snapshot: PrivateIndexSnapshot | None = None, artifact_root: Path | None = None,
                 git_ca_file: Path | None = None, index_credential_root: Path | None = None,
                 index_ca_file: Path | None = None, direct_ca_file: Path | None = None,
                 direct_download=None, release_catalog=None):
        self.store = store.resolve()
        self.uv_binary = uv_binary
        self.image_baseline = image_baseline
        self.private_snapshot = private_snapshot
        self.artifact_root = artifact_root
        # Explicit registry release catalog for coherent core upgrades; the
        # default reads the same approved public index from PEP 691 JSON.
        self.release_catalog = release_catalog
        # Explicit operator CA for disposable HTTPS fixtures only; never a bypass.
        self.git_ca_file = git_ca_file
        # Explicit operator-provisioned restricted root of per-label reference files;
        # the operator root is read here, unattended, for every private-policy build.
        self.index_credential_root = Path(index_credential_root) if index_credential_root is not None else None
        # Explicit operator CA for private index TLS verification; never a bypass.
        # Explicit operator CA for disposable HTTPS fixtures only; never a bypass.
        self.index_ca_file = index_ca_file
        # Explicit operator CA and/or injected retrieval for disposable HTTPS
        # direct-URL fixtures; production uses verified TLS with no bypass.
        self.direct_ca_file = direct_ca_file
        self.direct_download = direct_download

    @staticmethod
    def _git_authorizations(inputs: dict[str, Any]) -> dict:
        """Refuse credential references without a resolved restricted context.

        The in-process poller injects resolved authorization payloads; a container
        or direct builder call must never fall back to an unauthenticated fetch.
        """
        references = inputs.get('git_credentials') or {}
        authorizations = inputs.get('git_authorizations')
        if references and authorizations is None:
            raise PreparationError('Git credentials require the restricted in-process preparation worker')
        if authorizations is None:
            return {}
        if not isinstance(authorizations, dict):
            raise PreparationError('invalid Git credential context')
        if set(references) - set(authorizations):
            raise PreparationError('Git credential reference unavailable; reauthorize or provide a new source')
        return authorizations

    def _resolve_private_snapshot(self, inputs: dict[str, Any]):
        """Private-index references for this build, or None when no private policy applies.

        A configured operator credential root is authoritative and is loaded only
        when the effective config declares a private policy; without a root, a
        proof/test-injected snapshot is used.
        """
        if self.index_credential_root is None:
            return self.private_snapshot
        from .private_index import PrivateIndexSnapshot, effective_config, load_operator_references
        config = effective_config(inputs)
        if 'private_indexes' not in config and 'private_bindings' not in config:
            return None
        return PrivateIndexSnapshot(load_operator_references(self.index_credential_root), ca_file=self.index_ca_file)

    def _run(self, arguments: list[str], directory: Path, cache: Path, venv: Path,
             *, install_into_venv: bool = False) -> None:
        # Do not inherit UV/PIP configuration, credentials or live runtime paths.
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(directory),
               'UV_CACHE_DIR': str(cache), 'UV_PROJECT_ENVIRONMENT': str(venv),
               'UV_PYTHON_DOWNLOADS': 'never', 'UV_NO_PROGRESS': '1'}
        python = sys.executable
        if install_into_venv:
            target = venv / 'bin/python'
            if (arguments[:2] != ['pip', 'install'] or venv.parent != directory.parent
                    or venv.name != 'disposable-venv' or venv.is_symlink() or not target.is_file()
                    or target.resolve() != Path(sys.executable).resolve()):
                raise PreparationError('invalid disposable installation interpreter')
            python = str(target)
        if self.private_snapshot is not None:
            arguments = [*arguments, '--default-index', (directory / 'private-indexes' / 'snapshot').as_uri() + '/']
            if '--offline' not in arguments:
                arguments.append('--offline')
        completed = subprocess.run([self.uv_binary, *arguments, '--python', python], cwd=directory, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        if completed.returncode:
            # Raw uv/build output can contain credentials; never persist or expose it.
            raise PreparationError('registry preparation command failed')

    def build(self, inputs: dict[str, Any], *, retain_direct=None) -> dict[str, Any]:
        if inputs.get('change_operation') == 'rollback':
            return self._build_rollback(inputs)
        baseline = inputs['baseline']
        if baseline['target'] != python_target():
            raise PreparationError('unsupported builder target, tool or index policy')
        self.private_snapshot = self._resolve_private_snapshot(inputs)
        if self.private_snapshot is None:
            validate_public_policy(inputs)
        else:
            from .private_index import validate_private_policy
            validate_private_policy(inputs)
        # Refuse unresolved credential references before invoking any tooling.
        authorizations = self._git_authorizations(inputs)
        if os.geteuid() == 0:
            raise PreparationError('preparation builds require a dedicated non-root identity')
        version = subprocess.run([self.uv_binary, '--version'], capture_output=True, check=True).stdout.decode()
        if version.split()[:2] != ['uv', UV_VERSION]:
            raise PreparationError('builder uv release mismatch')
        operation = inputs.get('change_operation', 'add')
        if operation not in ('add', 'update', 'remove', 'config', 'repair', 'upgrade', 'adoption', 'migration'):
            raise PreparationError('unsupported managed operation')
        local_artifacts = retained_artifacts(inputs, self.artifact_root)
        direct_sources: dict[str, dict] = {}
        if direct_declarations(inputs) or inputs['baseline'].get('direct_sources'):
            if self.artifact_root is None:
                raise PreparationError('explicit artifact store configuration required')
            direct_sources, direct_artifacts = resolve_direct_sources(inputs, self.artifact_root,
                                                                      download=self.direct_download,
                                                                      ca_file=self.direct_ca_file,
                                                                      retain=retain_direct)
            local_artifacts = {**local_artifacts, **direct_artifacts}
        if local_artifacts and self.private_snapshot is not None:
            raise PreparationError('uploaded distributions require public source policy')
        requirements = [row['declaration'] for row in inputs['contributions']
                        if row['id'] != inputs.get('remove_contribution_id') and
                        row['id'] not in inputs.get('replace_artifact_contributions', []) and
                        row['id'] not in inputs.get('replace_git_contributions', []) and
                        row['id'] not in inputs.get('replace_direct_contributions', [])]
        if operation == 'add':
            requirements.append(inputs['requirement']['declaration'])
        requirements = [registry_declaration(value) for value in requirements]
        image_wheels: dict[str, Path] = {}
        image_registry_wheels: dict[str, ImageRegistryWheel] = {}
        if baseline.get('image_baseline'):
            from docassemble.base.managed_qualification import require_qualified_inputs
            from .image_baseline import load_image_wheels, load_image_registry_wheels
            from .paths import ExecutorPaths
            if self.image_baseline is None:
                raise PreparationError('image baseline requires explicit operator configuration')
            trusted, image_wheels = load_image_wheels(self.image_baseline, ExecutorPaths.from_env())
            require_qualified_inputs(inputs, trusted)
            image_registry_wheels = load_image_registry_wheels(self.image_baseline, ExecutorPaths.from_env())
            if baseline.get('selected_baseline'):
                from .selected_baseline import selected_wheels
                image_wheels = selected_wheels(self.store, baseline['selected_baseline'])
        if operation == 'upgrade':
            return self._build_upgrade(inputs, baseline, requirements, local_artifacts,
                                       direct_sources, authorizations, image_registry_wheels)
        core = baseline['core']
        requirements.extend(name + '==' + value['version'] for name, value in core.items())
        validate_artifact_requirements(requirements, local_artifacts)
        return self._solve(inputs, operation=operation, core=core, requirements=requirements,
                           image_wheels=image_wheels, local_artifacts=local_artifacts,
                           direct_sources=direct_sources, authorizations=authorizations,
                           image_registry_wheels=image_registry_wheels,
                           seed_parent='required' if operation in ('update', 'remove', 'migration') else None)

    def _solve(self, inputs: dict[str, Any], *, operation: str, core: dict[str, Any],
               requirements: list[str], image_wheels: dict[str, Path],
               local_artifacts: dict[str, tuple[dict[str, str], Path]], direct_sources: dict[str, dict],
               authorizations: dict, seed_parent: str | None, validation: dict[str, Any] | None = None,
               validate_result: bool = True, materialize: bool = False,
               image_registry_wheels: dict[str, ImageRegistryWheel] | None = None) -> dict[str, Any]:
        baseline = inputs['baseline']
        self.store.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='prepare-') as temporary:
            work = Path(temporary)
            project = work / 'project'
            project.mkdir()
            header = '[project]\nname = "managed-candidate"\nversion = "0"\nrequires-python = "' + python_requires() + '"\ndependencies = '
            policy = '\n[tool.uv]\npackage = false\n\n[[tool.uv.index]]\nurl = "https://pypi.org/simple"\ndefault = true\n'
            cache, venv = work / 'cache', work / 'disposable-venv'
            (project / 'sources').mkdir()
            git_sources = resolve_sources(inputs, work, authorizations=authorizations, ca_file=self.git_ca_file)
            if git_sources and self.private_snapshot is not None:
                raise PreparationError('Git distributions require public source policy')
            git_wheels = build_git_wheels(git_sources, work, project,
                lambda args: self._run(args, project, cache, venv),
                authorizations=authorizations, ca_file=self.git_ca_file)
            metadata_artifacts = dict(local_artifacts)
            metadata_artifacts.update({name: ({'version': str(parse_wheel_filename(wheel.name)[1])}, wheel)
                                       for name, wheel in git_wheels.items()})
            validate_artifact_requirements(requirements, metadata_artifacts)
            image_bindings = []
            (project / 'wheels').mkdir()
            git_bindings = []
            for name, wheel in git_wheels.items():
                shutil.copyfile(wheel, project / 'wheels' / wheel.name)
                git_bindings.append(json.dumps(name) + ' = { path = ' + json.dumps('wheels/' + wheel.name) + ' }')
            for name, wheel in image_wheels.items():
                shutil.copyfile(wheel, project / 'wheels' / wheel.name)
                image_bindings.append(json.dumps(name) + ' = { path = ' + json.dumps('wheels/' + wheel.name) + ' }')
            local_bindings = []
            (project / 'artifacts').mkdir()
            for name, (identity, path) in local_artifacts.items():
                destination = project / 'artifacts' / identity['filename']
                shutil.copyfile(path, destination)
                if sha256_file(destination) != identity['digest']:
                    raise PreparationError('distribution artifact digest mismatch')
                local_bindings.append(json.dumps(name) + ' = { path = ' + json.dumps('artifacts/' + destination.name) + ' }')
            private_artifacts: dict[str, Path] = {}
            if self.private_snapshot is not None:
                private_policy, private_artifacts = self.private_snapshot.prepare(inputs, project)
                policy = '\n[tool.uv]\npackage = false\n' + private_policy
            (project / 'pyproject.toml').write_text(header + json.dumps(requirements) + policy +
                ('\n[tool.uv.sources]\n' + '\n'.join(image_bindings + local_bindings + git_bindings) + '\n' if image_bindings or local_bindings or git_bindings else ''))
            lock_arguments = ['lock', '--no-config']
            if seed_parent:
                # Seed uv's preferences with the verified parent registry solve,
                # never hard constraints that would prevent necessary movement.
                parent_digest = baseline.get('publication', {}).get('result', {}).get('bundle', {}).get('digest')
                retained_parent = (isinstance(parent_digest, str) and len(parent_digest) == 64 and
                                   all(char in '0123456789abcdef' for char in parent_digest) and
                                   (self.store / parent_digest).is_dir())
                if retained_parent:
                    parent = self.store / parent_digest
                    verify_retained(parent, parent_digest)
                    try:
                        parent_matches = (verify_python_target(parent / 'solve-project.toml', parent / 'solve.lock')
                            and json.loads((parent / 'tool.json').read_text()) ==
                            {'uv': UV_VERSION, 'target': python_target()})
                    except (OSError, ValueError, TypeError):
                        parent_matches = False
                    if not parent_matches:
                        raise PreparationError('unsupported builder target, tool or index policy')
                    shutil.copyfile(parent / 'solve.lock', project / 'uv.lock')
                    if operation == 'update':
                        lock_arguments.extend(['--upgrade-package', inputs['requirement']['normalized_name']])
                elif seed_parent == 'required':
                    raise PreparationError('update/remove requires a retained parent solve')
            if operation == 'migration':
                # A replacement can ship different wheel bytes at the same
                # version/path. Unlock fixed target core metadata, not unrelated
                # registry version preferences or any Git/archive identity.
                for name in sorted(core):
                    lock_arguments.extend(['--upgrade-package', name])
            self._run(lock_arguments, project, cache, venv)
            if not verify_python_target(project / 'pyproject.toml', project / 'uv.lock'):
                raise PreparationError('unsupported builder target, tool or index policy')
            shutil.copyfile(project / 'uv.lock', project / 'solve.lock')
            shutil.copyfile(project / 'pyproject.toml', project / 'solve-project.toml')
            lock = tomllib.loads((project / 'uv.lock').read_text())
            inventory: dict[str, Any] = {}
            bindings = []
            artifacts = [sha256_file(path) for path in (project / 'sources').iterdir()] + [
                sha256_file(path) for path in git_wheels.values()] + [sha256_file(path) for path in image_wheels.values()] + [
                identity['digest'] for identity, _ in local_artifacts.values()]
            for package in lock['package']:
                if package['name'] == 'managed-candidate' and package['source'] == {'virtual': '.'}:
                    continue
                expected_registry = INDEX
                if self.private_snapshot is not None:
                    expected_registry = str(project / 'private-indexes' / 'snapshot') + '/'
                if package['source'] != {'registry': expected_registry} and not (
                        package['name'] in image_wheels and package['source'] ==
                        {'path': 'wheels/' + image_wheels[package['name']].name}) and not (
                        package['name'] in local_artifacts and package['source'] ==
                        {'path': 'artifacts/' + local_artifacts[package['name']][0]['filename']}) and not (
                        package['name'] in git_wheels and package['source'] ==
                        {'path': 'wheels/' + git_wheels[package['name']].name}):
                    raise PreparationError('unsupported transitive source; declare an explicit private binding or use registry requirements')
                # Materialize via uv first to select target-compatible wheels and build sdists.
                name = canonicalize_name(package['name'])
                if name in inventory:
                    raise PreparationError('unsupported multi-version target lock')
                inventory[name] = {'version': package['version'], 'source_digest': ''}
            reused = self._reuse_registry_wheels(lock, image_registry_wheels or {}, project)
            sync_arguments = ['sync', '--frozen', '--no-config']
            for name in sorted(reused):
                sync_arguments.extend(['--no-install-package', name])
            self._run(sync_arguments, project, cache, venv)
            if reused:
                self._run(['pip', 'install', '--no-deps', '--no-index', '--no-build', '--offline', '--no-config',
                           *[str(project / 'wheels' / reused[name].path.name) for name in sorted(reused)]],
                          project, cache, venv, install_into_venv=True)
            actual = inspect_environment(venv)
            if any(actual.get(name) != wheel.origin['version'] for name, wheel in reused.items()):
                raise PreparationError('substituted registry inventory mismatch')
            validate_artifact_requirements(requirements, metadata_artifacts, actual)
            if image_wheels or local_artifacts or git_wheels or materialize:
                # Universal locks can contain non-Linux marker branches. The
                # exact image target selects inventory, never a new core solve.
                inventory = {name: value for name, value in inventory.items() if name in actual}
            if actual != {n: v['version'] for n, v in inventory.items()}:
                raise PreparationError('resolved inventory mismatch')
            for package in lock['package']:
                name = canonicalize_name(package['name'])
                local = local_artifacts.get(name)
                local = local if local and package['source'] == {'path': 'artifacts/' + local[0]['filename']} else None
                if name not in inventory and local is None:
                    continue
                git_wheel = git_wheels.get(name)
                image_wheel = image_wheels.get(name)
                if git_wheel and package['source'] == {'path': 'wheels/' + git_wheel.name}:
                    source_identity = git_sources.get(name)
                    if source_identity is None:
                        raise PreparationError('built metadata identity mismatch')
                    inventory[name]['source_digest'] = git_source_digest(source_identity)
                    bindings.append(json.dumps(name) + ' = { path = ' + json.dumps('wheels/' + git_wheel.name) + ' }')
                    continue
                if image_wheel and package['source'] == {'path': 'wheels/' + image_wheel.name}:
                    # Only an actually path-bound core row takes image authority.
                    # Registry-resolved transitive intent retains its original sdist.
                    core_identity = core.get(name)
                    if core_identity is None or 'source_digest' not in core_identity:
                        raise PreparationError('exact core source baseline changed')
                    inventory[name]['source_digest'] = core_identity['source_digest']
                    bindings.append(json.dumps(name) + ' = { path = ' + json.dumps('wheels/' + image_wheel.name) + ' }')
                    continue
                # Retain the source actually selected by the lock, not caller intent.
                compatible_wheel = None
                if package['source'] == {'registry': INDEX}:
                    tags = set(sys_tags())
                    compatible_wheel = next((wheel for wheel in package.get('wheels', []) if
                        parse_wheel_filename(Path(urllib.parse.urlsplit(wheel.get('url', '')).path).name)[3] & tags), None)
                source = {'path': 'artifacts/' + local[0]['filename'], 'hash': 'sha256:' + local[0]['digest']} if local else package.get('sdist')
                if source is None:
                    wheels = package.get('wheels', [])
                    tags = set(sys_tags())
                    source = next((wheel for wheel in wheels if
                        parse_wheel_filename(Path(urllib.parse.urlsplit(wheel.get('url', wheel.get('path', ''))).path).name)[3] & tags), None)
                if source is None:
                    raise PreparationError('unsupported wheel-only target')
                source_url = source.get('url', source.get('path', ''))
                url = urllib.parse.urlsplit(source_url)
                filename = Path(url.path).name
                destination = project / 'sources' / filename
                if local:
                    shutil.copyfile(project / source_url, destination)
                elif self.private_snapshot is not None:
                    original = private_artifacts.get(source_url)
                    if original is None:
                        raise PreparationError('unsupported transitive artifact; private distribution snapshot required')
                    if source['hash'] != 'sha256:' + sha256_file(Path(source_url)):
                        raise PreparationError('private build input digest mismatch')
                    shutil.copyfile(original, destination)
                else:
                    if url.scheme != 'https' or url.hostname != 'files.pythonhosted.org' or url.query or url.username:
                        raise PreparationError('unsupported artifact URL')
                    with _open_registry_artifact(source['url']) as response:
                        if urllib.parse.urlsplit(response.geturl()).hostname != 'files.pythonhosted.org':
                            raise PreparationError('unsupported artifact redirect')
                        destination.write_bytes(response.read())
                source_hash = sha256_file(destination)
                if self.private_snapshot is None and source['hash'] != 'sha256:' + source_hash:
                    raise PreparationError('source artifact digest mismatch')
                if name in inventory:
                    inventory[name]['source_digest'] = source_hash
                artifacts.append(source_hash)
                if name in reused:
                    from .image_baseline import validate_registry_wheel
                    validate_registry_wheel(project / 'wheels' / reused[name].path.name,
                                            dict(reused[name].origin, wheel_hash=reused[name].wheel_hash))
                elif filename.endswith('.whl'):
                    shutil.copyfile(destination, project / 'wheels' / filename)
                elif compatible_wheel is not None:
                    # Registry solve provenance stays unchanged; retain the locked
                    # published bytes for the separate cold, all-local apply.
                    wheel_url = urllib.parse.urlsplit(compatible_wheel['url'])
                    wheel_hash = compatible_wheel.get('hash')
                    if (wheel_url.scheme != 'https' or wheel_url.hostname != 'files.pythonhosted.org'
                            or wheel_url.query or wheel_url.fragment or wheel_url.username or wheel_url.password
                            or not isinstance(wheel_hash, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', wheel_hash)):
                        raise PreparationError('unsupported artifact URL')
                    wheel_destination = project / 'wheels' / Path(wheel_url.path).name
                    with _open_registry_artifact(compatible_wheel['url']) as response:
                        redirected = urllib.parse.urlsplit(response.geturl())
                        if (redirected.scheme != 'https' or redirected.hostname != 'files.pythonhosted.org'
                                or redirected.query or redirected.fragment or redirected.username or redirected.password):
                            raise PreparationError('unsupported artifact redirect')
                        wheel_destination.write_bytes(response.read())
                    if wheel_hash != 'sha256:' + sha256_file(wheel_destination):
                        raise PreparationError('source artifact digest mismatch')
                else:
                    # The registry solve and independent wheel build must both
                    # use the shimmed copy, never execute the original unguarded.
                    build_source = Path(source_url) if self.private_snapshot is not None else destination
                    if self.private_snapshot is not None:
                        artifacts.append(sha256_file(build_source))
                    self._run(['build', '--wheel', '--no-config', '--out-dir', str(project / 'wheels'),
                               str(build_source)], project, cache, venv)
                matching = [p for p in (project / 'wheels').glob('*.whl')
                            if canonicalize_name(parse_wheel_filename(p.name)[0]) == name]
                if len(matching) != 1 or str(parse_wheel_filename(matching[0].name)[1]) != package['version']:
                    raise PreparationError('built metadata identity mismatch')
                if self.private_snapshot is not None:
                    from .private_index import effective_config
                    self.private_snapshot._validate_wheel(matching[0], name,
                        effective_config(inputs)['private_bindings'], core)
                artifacts.append(sha256_file(matching[0]))
                bindings.append(json.dumps(name) + ' = { path = ' + json.dumps('wheels/' + matching[0].name) + ' }')
            if reused:
                (project / 'image-registry-artifacts.json').write_text(json.dumps({name: {
                    'source': wheel.origin, 'wheel': 'wheels/' + wheel.path.name,
                    'wheel_hash': wheel.wheel_hash, 'image_baseline': wheel.image_identity}
                    for name, wheel in reused.items()}, sort_keys=True))
            if any(inventory.get(name) != value for name, value in core.items()):
                raise PreparationError('exact core source baseline changed')
            # The solve provenance remains separate. The apply project references only retained wheels.
            apply_requirements = requirements + [name + '==' + value['version'] for name, value in inventory.items()]
            (project / 'pyproject.toml').write_text(header + json.dumps(apply_requirements) +
                '\n[tool.uv]\npackage = false\n[tool.uv.sources]\n' + '\n'.join(bindings) + '\n')
            (project / 'uv.lock').unlink()
            self._run(['lock', '--offline', '--no-config'], project, cache, venv)
            if not verify_python_target(project / 'pyproject.toml', project / 'uv.lock'):
                raise PreparationError('unsupported builder target, tool or index policy')
            shutil.rmtree(venv)
            shutil.rmtree(cache)
            self._run(['sync', '--frozen', '--no-build', '--offline', '--no-config'], project, cache, venv)
            material = wheel_payload_material(project / 'wheels') if (image_wheels or materialize or reused) else None
            if not verify_environment(venv, {n: v['version'] for n, v in inventory.items()}, material).ok:
                raise PreparationError('cold-cache materialization mismatch')
            if operation in ('upgrade-core', 'upgrade') and not os.access(venv / 'bin' / 'gunicorn', os.X_OK):
                raise PreparationError('cold-cache materialization requires protected Gunicorn executable')
            if material is not None:
                (project / 'installed-material.json').write_text(json.dumps(material, sort_keys=True))
            (project / 'inventory.json').write_text(json.dumps(inventory, sort_keys=True))
            (project / 'tool.json').write_text(json.dumps({'uv': UV_VERSION, 'target': baseline['target']}))
            manifest = {p.relative_to(project).as_posix(): sha256_file(p)
                        for p in project.rglob('*') if p.is_file()}
            bundle_digest = digest(manifest)
            (project / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
            # Copy, fsync all bytes, then atomically expose a content-addressed directory.
            staging = Path(tempfile.mkdtemp(prefix='.retain-', dir=self.store))
            try:
                shutil.copytree(project, staging, dirs_exist_ok=True)
                for path in staging.rglob('*'):
                    if path.is_file():
                        path.chmod(0o600)
                        with path.open('rb') as handle:
                            os.fsync(handle.fileno())
                for directory in [p for p in staging.rglob('*') if p.is_dir()] + [staging]:
                    directory.chmod(0o700)
                    fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                retained = self.store / bundle_digest
                if retained.exists():
                    verify_retained(retained, bundle_digest)
                else:
                    publish_retained(staging, retained, bundle_digest)
                fd = os.open(self.store, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                verify_retained(retained, bundle_digest)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            def diff(before):
                return {n: {'before': before.get(n), 'after': inventory.get(n)}
                        for n in sorted(set(before) | set(inventory)) if before.get(n) != inventory.get(n)}
            known = baseline['runtime']['kind'] == 'normal'
            result = {'bundle': {'digest': bundle_digest,
                      'project_digest': sha256_file(retained / 'pyproject.toml'),
                      'lock_digest': sha256_file(retained / 'uv.lock'),
                      'inventory': inventory, 'artifacts': artifacts},
                      'desired_diff': diff(baseline['desired_inventory']),
                      'runtime_diff': diff(baseline['runtime']['inventory']) if known else None,
                      'warnings': Preparation.ready_warnings(inputs),
                      'validation': validation if validation is not None else {
                          'core_unchanged': True, 'requirements_satisfied': True,
                          'durable_materialization_verified': True},
                      'ownership_effects': inputs['ownership_effects']}
            if git_sources or baseline.get('git_sources'):
                result['git_sources'] = git_sources
            if direct_sources or baseline.get('direct_sources'):
                result['direct_sources'] = direct_sources
            if operation == 'config':
                result['config_diff'] = Preparation.config_diff(inputs)
            if validate_result:
                Preparation._validate_result(inputs, result)
            return result

    def _build_rollback(self, inputs):
        """Reverify and cold-apply the recorded project; never invoke a solver."""
        from docassemble.base.managed_rollback import result_for
        from .selected_baseline import selected_directory
        from .rollback_schema import retained_schema_policy
        # Frozen retained wheels require no index snapshot or source credentials.
        self.private_snapshot = None
        historical = inputs['rollback']['snapshot']
        baseline = inputs['baseline']
        if any(historical.get(key) != baseline.get(key) for key in ('target', 'tool', 'image_baseline')):
            raise PreparationError('historical target mismatch')
        if baseline['target'] != python_target() or os.geteuid() == 0:
            raise PreparationError('rollback preparation requires the qualified non-root Python target')
        if subprocess.run([self.uv_binary, '--version'], capture_output=True, check=True).stdout.decode().split()[:2] != ['uv', UV_VERSION]:
            raise PreparationError('builder uv release mismatch')
        bundle = historical['publication']['result']['bundle']
        directory = self.store / bundle['digest']
        verify_retained(directory, bundle['digest'])
        if (not verify_python_target(directory / 'pyproject.toml', directory / 'uv.lock') or
                not verify_python_target(directory / 'solve-project.toml', directory / 'solve.lock') or
                sha256_file(directory / 'pyproject.toml') != bundle['project_digest'] or
                sha256_file(directory / 'uv.lock') != bundle['lock_digest'] or
                json.loads((directory / 'inventory.json').read_text()) != bundle['inventory'] or
                json.loads((directory / 'tool.json').read_text()) != {'uv': UV_VERSION, 'target': baseline['target']}):
            raise PreparationError('historical bundle identity mismatch')
        manifest = json.loads((directory / 'manifest.json').read_text())
        if not set(bundle['artifacts']).issubset(set(manifest.values())):
            raise PreparationError('historical artifacts unavailable')
        selected = historical.get('selected_baseline')
        if selected:
            selected_directory(self.store, selected)
        elif self.image_baseline is not None:
            from .image_baseline import load_image_wheels, verify_image_recovery_core
            from .paths import ExecutorPaths
            verify_image_recovery_core(ExecutorPaths.from_env())
            image, _ = load_image_wheels(self.image_baseline, ExecutorPaths.from_env())
            if any(image[key] != historical[key] for key in ('core', 'target', 'tool', 'image_baseline')):
                raise PreparationError('historical image core mismatch')
        else:
            raise PreparationError('retained historical core unavailable')
        retained_schema_policy(directory)
        retained_artifacts({**inputs, 'baseline': historical}, self.artifact_root)
        from docassemble.base.managed_artifacts import verify_artifact
        for source in historical.get('direct_sources', {}).values():
            if self.artifact_root is None:
                raise PreparationError('historical direct artifact unavailable')
            verify_artifact(self.artifact_root, source['digest'])
        with tempfile.TemporaryDirectory(prefix='rollback-check-') as temporary:
            root = Path(temporary)
            self._run(['sync', '--frozen', '--offline', '--no-build', '--no-config'], directory, root / 'cache', root / 'venv')
            material = json.loads((directory / 'installed-material.json').read_text())
            if not verify_environment(root / 'venv', {name: value['version'] for name, value in bundle['inventory'].items()}, material).ok:
                raise PreparationError('historical cold materialization mismatch')
        result = result_for(inputs)
        Preparation._validate_result(inputs, result)
        return result

    def _reuse_registry_wheels(self, lock: dict, wheels: dict[str, ImageRegistryWheel],
                               project: Path) -> dict[str, ImageRegistryWheel]:
        from packaging.markers import Marker
        from .image_baseline import registry_sdist_identity, validate_registry_wheel
        if not wheels or self.private_snapshot is not None:
            return {}
        # Only reachable, concrete-target required dependencies are eligible.
        # Optional/unknown branches are not promoted into the installed target.
        rows = {canonicalize_name(row['name']): row for row in lock['package']}
        active = set()
        pending = ['managed-candidate']
        while pending:
            name = pending.pop()
            if name in active or name not in rows:
                continue
            row = rows[name]
            markers = row.get('resolution-markers', [])
            if markers and not any(Marker(marker).evaluate({'extra': ''}) for marker in markers):
                continue
            active.add(name)
            for dependency in row.get('dependencies', []):
                if not dependency.get('marker') or Marker(dependency['marker']).evaluate({'extra': ''}):
                    pending.append(canonicalize_name(dependency['name']))
        reused = {}
        for name, wheel in wheels.items():
            if name not in active:
                continue
            row = rows[name]
            try:
                source = registry_sdist_identity(row)
            except PreparationError:
                continue
            if source != wheel.origin:
                continue
            destination = project / 'wheels' / wheel.path.name
            validate_registry_wheel(wheel.path, dict(wheel.origin, wheel_hash=wheel.wheel_hash))
            if destination.exists() or destination.is_symlink():
                try:
                    validate_registry_wheel(destination, dict(wheel.origin, wheel_hash=wheel.wheel_hash))
                except PreparationError:
                    raise PreparationError('conflicting image registry artifact') from None
            else:
                shutil.copyfile(wheel.path, destination)
                validate_registry_wheel(destination, dict(wheel.origin, wheel_hash=wheel.wheel_hash))
            reused[name] = wheel
        return reused

    def _build_upgrade(self, inputs: dict[str, Any], baseline: dict[str, Any], requirements: list[str],
                       local_artifacts: dict[str, tuple[dict[str, str], Path]], direct_sources: dict[str, dict],
                       authorizations: dict,
                       image_registry_wheels: dict[str, ImageRegistryWheel] | None = None) -> dict[str, Any]:
        """Fresh coherent core solve, then the full user solve around the frozen core."""
        if self.private_snapshot is not None:
            raise PreparationError('coherent upgrade requires the approved public index policy')
        selection = parse_release_selection(inputs.get('release_selection'))
        catalog = self.release_catalog or default_release_catalog(baseline['config']['index_url'])
        selected = select_release(catalog.versions(WEBAPP), selection)
        coherent = coherent_core(selected, catalog.requires_dist(WEBAPP, selected))
        reject_downgrade(coherent, baseline['core'])
        core_inputs = self._core_solve_inputs(coherent, baseline)
        core_result = self._solve(core_inputs, operation='upgrade-core', core={},
                                  requirements=core_requirements(coherent) + ['gunicorn'], image_wheels={},
                                  local_artifacts={}, direct_sources={}, authorizations={},
                                  seed_parent=None, validate_result=False, materialize=True,
                                  image_registry_wheels=image_registry_wheels)
        core_bundle = core_result['bundle']
        core_inputs['baseline']['core'] = core_bundle['inventory']
        Preparation._validate_result(core_inputs, core_result)
        core = core_bundle['inventory']
        if any(core.get(name, {}).get('version') != coherent[name] for name in (BASE, WEBAPP, DEMO)):
            raise PreparationError('selected release core inventory mismatch')
        if 'gunicorn' not in core:
            raise PreparationError('selected release core requires protected Gunicorn root')
        from .selected_baseline import selected_wheels
        core_wheels = selected_wheels(self.store, {'core_bundle': core_bundle})
        full_requirements = requirements + [name + '==' + value['version'] for name, value in core.items()]
        validate_artifact_requirements(full_requirements, local_artifacts)
        result = self._solve(inputs, operation='upgrade', core=core, requirements=full_requirements,
                             image_wheels=core_wheels, local_artifacts=local_artifacts, direct_sources=direct_sources,
                             authorizations=authorizations, seed_parent='preferred', validate_result=False,
                             image_registry_wheels=image_registry_wheels,
                             validation={'core_matches_release': True, 'requirements_satisfied': True,
                                         'durable_materialization_verified': True}, materialize=True)
        result['core_bundle'] = core_bundle
        result['release'] = {'selection': {'mode': selection.mode, 'version': selection.version},
                             'resolved': coherent[WEBAPP], 'webapp': coherent[WEBAPP],
                             'base': coherent[BASE], 'demo': coherent[DEMO],
                             'core_bundle': core_bundle['digest']}
        result['risk_disclosure'] = upgrade_risk_disclosure()
        Preparation._validate_result(inputs, result)
        return result

    @staticmethod
    def _core_solve_inputs(coherent: dict[str, str], baseline: dict[str, Any]) -> dict[str, Any]:
        pins = {name: {'version': coherent[name]} for name in (BASE, WEBAPP, DEMO)}
        return {'baseline': {'target': baseline['target'], 'tool': baseline['tool'],
                             'config': baseline['config'], 'core': pins,
                             'desired_inventory': {}, 'runtime': {'kind': 'normal', 'inventory': {}}},
                'change_operation': 'upgrade-core', 'ownership_effects': {}, 'contributions': []}
