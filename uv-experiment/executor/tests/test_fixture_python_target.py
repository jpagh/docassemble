"""Image construction builds sdists; every retained/runtime sync forbids builds."""
from pathlib import Path
import tomllib
import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_fixture_python_target_pinning_protected_provisioning_and_templates():
    script = (ROOT / 'uv-experiment/fixture/image-build.sh').read_text()
    assert 'MANAGED_PYTHON_VERSION=3.14.4' in script
    assert 'uv python install "$MANAGED_PYTHON_VERSION"' in script
    assert 'uv python find --managed-python "$MANAGED_PYTHON_VERSION"' in script
    assert 'UV_PYTHON=$(uv python find --managed-python' in script and 'export UV_PYTHON' in script
    assert 'Path(sys.executable).resolve() == Path(sys.argv[3]).resolve()' in script
    assert 'runtime_venv' in script and "paths.da_root / 'local3.14'" in script
    assert 'RUNTIME_VENV=/usr/share/docassemble/local' not in script
    for template in [ROOT / 'uv-experiment/executor/pyproject.toml',
                     *(ROOT / 'uv-experiment/fixture/bundles').glob('*/pyproject.toml')]:
        assert tomllib.loads(template.read_text())['project']['requires-python'] == '==3.14.*'


def test_fixture_sdist_build_is_image_only_and_retained_sync_is_no_build():
    script = (ROOT / 'uv-experiment/fixture/image-build.sh').read_text()
    build = 'UV_PROJECT_ENVIRONMENT="$RUNTIME_VENV" uv sync --frozen --project "$BUNDLE_ROOT/core-r1"'
    retain = '"$FIXTURE/materialize_wheels.py" "$BUNDLE_ROOT/$name" --retain-built'
    apply = 'uv sync --frozen --no-build --project "$BUNDLE_ROOT/$name"'
    assert script.index(retain) < script.index(build) < script.index(apply)
    runtime = (ROOT / 'uv-experiment/executor/src/docassemble_executor/uvsync.py').read_text()
    assert "if not allow_builds:\n        command.append('--no-build')" in runtime
    replacement = (ROOT / 'uv-experiment/fixture/Dockerfile.replacement-proof').read_text()
    assert '/ticket/executor/docassemble_executor' in replacement
    assert 'sysconfig.get_path("purelib")' in replacement
    assert 'lib/python3.12' not in replacement
    assert '/usr/share/docassemble/executor-venv/bin/python' in replacement


def test_materialize_wheels_origin_matches_actual_locked_sdist(tmp_path, monkeypatch):
    import importlib.util
    import json
    from registry_wheel_fixture import reusable
    spec = importlib.util.spec_from_file_location('materialize_wheels', ROOT / 'uv-experiment/fixture/materialize_wheels.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    artifact = reusable(tmp_path / 'build')
    (artifact.path.parent / 'source-artifacts.json').write_text(json.dumps({artifact.path.name:
        dict(artifact.origin, wheel_hash=artifact.wheel_hash)}))
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'pyproject.toml').write_text('[project]\nrequires-python="==3.14.*"\ndependencies = []\n[tool.uv.sources]\n')
    import tomli_w
    (bundle / 'uv.lock').write_text(tomli_w.dumps({'requires-python': '==3.14.*', 'package': [artifact.origin]}))
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: pytest.fail('sharing never builds/solves'))
    before = (bundle / 'pyproject.toml').read_bytes()
    bad = dict(artifact.origin, sdist=dict(artifact.origin['sdist'], hash='sha256:' + 'b' * 64))
    (bundle / 'uv.lock').write_text(tomli_w.dumps({'requires-python': '==3.14.*', 'package': [bad]}))
    with pytest.raises(ValueError, match='origin mismatch'):
        module.retain_built(bundle, artifact.path.parent)
    assert (bundle / 'pyproject.toml').read_bytes() == before
    (bundle / 'uv.lock').write_text(tomli_w.dumps({'requires-python': '==3.14.*', 'package': [artifact.origin]}))
    module.retain_built(bundle, artifact.path.parent)
    assert tomllib.loads((bundle / 'pyproject.toml').read_text())['tool']['uv']['sources']['numpy'] == {'path': 'wheels/' + artifact.path.name}
    assert json.loads((bundle / 'source-artifacts.json').read_text())[artifact.path.name]['wheel_hash'] == artifact.wheel_hash


def test_runtime_helper_rendering_is_fail_closed_and_used_in_both_builds(tmp_path, monkeypatch):
    import importlib.util
    from types import SimpleNamespace
    renderer = ROOT / 'uv-experiment/fixture/render_runtime_helpers.py'
    spec = importlib.util.spec_from_file_location('render_runtime_helpers', renderer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = tmp_path / 'managed-runtime3.14'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'bin/python').touch()
    for path in ('opt/docassemble-fixture', 'etc/exim4'):
        (tmp_path / path).mkdir(parents=True)
    for name in ('helper-crontab', 'helper-exim.conf'):
        (tmp_path / 'opt/docassemble-fixture' / name).write_text('@MANAGED_RUNTIME@')
    monkeypatch.setattr(module.ExecutorPaths, 'from_env', lambda: SimpleNamespace(runtime_venv=runtime))
    monkeypatch.setattr(module, 'Path', lambda value: tmp_path / value.lstrip('/'))
    module.main()
    assert (tmp_path / 'etc/crontab').read_text() == str(runtime)
    assert (tmp_path / 'etc/exim4/exim4.conf').read_text() == str(runtime)
    (runtime / 'bin/python').unlink()
    with pytest.raises(AssertionError, match='interpreter missing'):
        module.main()
    for dockerfile in ('Dockerfile', 'Dockerfile.replacement-proof'):
        assert '/opt/docassemble-fixture/render_runtime_helpers.py' in (ROOT / 'uv-experiment/fixture' / dockerfile).read_text()
    for name in ('helper-crontab', 'helper-exim.conf'):
        assert '@MANAGED_RUNTIME@' in (ROOT / 'uv-experiment/fixture' / name).read_text()


@pytest.mark.parametrize('consumer', ['executor', 'core-build', 'runtime-variant'])
@pytest.mark.parametrize('fault', ['current', 'old-project', 'old-lock', 'missing-project', 'missing-lock'])
def test_primary_build_pair_preflight_precedes_copy_and_frozen_sync(tmp_path, consumer, fault):
    import json
    import os
    import subprocess
    import sys
    from docassemble.base.managed_python import python_requires
    script = (ROOT / 'uv-experiment/fixture/image-build.sh').read_text()
    helper = script.split('check_python_target() {', 1)[1].split('\n}\n', 1)[0]
    helper = 'check_python_target() {' + helper + '\n}\n'
    helper = helper.replace("['/opt/docassemble-executor/src', '/docassemble_base']",
        repr([str(ROOT / 'uv-experiment/executor/src'), str(ROOT / 'docassemble_base')]))
    executable = 'UV_PROJECT_ENVIRONMENT="$EXECUTOR_VENV" uv sync --frozen --project /opt/docassemble-executor'
    core = 'UV_PROJECT_ENVIRONMENT="$RUNTIME_VENV" uv sync --frozen --project "$BUNDLE_ROOT/core-r1"'
    variant = 'UV_PROJECT_ENVIRONMENT="$RUNTIME_VENV" uv sync --frozen --no-build --project "$BUNDLE_ROOT/$name"'
    command = {'executor': executable, 'core-build': core, 'runtime-variant': variant}[consumer]
    preceding = script[:script.index(command)].rstrip().splitlines()[-1].strip()
    assert preceding.startswith('check_python_target ')
    first_check = script.index('\ncheck_python_target /opt/docassemble-executor')
    assert first_check < script.index('cp -a "$SOURCE_ROOT/$project"') < script.index(executable)
    bundle = tmp_path / 'bundles' / 'core-r1'
    bundle.mkdir(parents=True)
    (bundle / 'pyproject.toml').write_text('[project]\nname="build-input"\nversion="0"\n'
        'requires-python=' + json.dumps('==3.12.*' if fault == 'old-project' else python_requires()) +
        '\ndependencies=[]\n[tool.uv]\npackage=false\n')
    (bundle / 'uv.lock').write_text('version=1\nrevision=3\nrequires-python=' +
        json.dumps('==3.12.*' if fault == 'old-lock' else python_requires()) + '\n'
        '[[package]]\nname="build-input"\nversion="0"\nsource={virtual="."}\n')
    if fault.startswith('missing'):
        (bundle / ('pyproject.toml' if fault.endswith('project') else 'uv.lock')).unlink()
    tools = tmp_path / 'tools'
    tools.mkdir()
    trace = tmp_path / 'consumed'
    uv = tools / 'uv'
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CONSUMPTION_TRACE"\n')
    uv.chmod(0o755)
    # Replay the actual guard immediately preceding each direct sync. The copy
    # spy models bootstrap material copying; source ordering above binds it too.
    command = command.replace('/opt/docassemble-executor', '"$BUNDLE_ROOT/core-r1"')
    preceding = preceding.replace('/opt/docassemble-executor', '"$BUNDLE_ROOT/core-r1"')
    program = 'set -euo pipefail\n' + helper + preceding + '\n' + command + '\necho copy >> "$CONSUMPTION_TRACE"\n'
    result = subprocess.run(['bash', '-c', program], capture_output=True, text=True, env=dict(os.environ,
        UV_PYTHON=sys.executable, BUNDLE_ROOT=str(bundle.parent), EXECUTOR_VENV=str(tmp_path / 'executor'),
        RUNTIME_VENV=str(tmp_path / 'runtime'), name='core-r1', CONSUMPTION_TRACE=str(trace),
        PATH=str(tools) + ':' + os.environ['PATH']))
    if fault == 'current':
        assert result.returncode == 0, result.stderr
        assert 'sync --frozen' in trace.read_text()
    else:
        assert result.returncode != 0
        assert 'retained project and lock Python target mismatch' in result.stderr
        assert not trace.exists()


def replacement_builder(tmp_path, monkeypatch):
    import importlib.util
    import json
    import sys
    from types import SimpleNamespace
    from docassemble.base.managed_python import python_requires
    path = ROOT / 'uv-experiment/fixture/replacement-image-build.py'
    spec = importlib.util.spec_from_file_location('replacement_image_build', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / 'bundles'
    for name in ('core-r1', 'runtime-r1'):
        directory = root / name
        (directory / 'wheels').mkdir(parents=True)
        (directory / 'pyproject.toml').write_text('[project]\nname="replacement-input"\nversion="0"\n'
            'requires-python=' + json.dumps(python_requires()) + '\ndependencies=[]\n[tool.uv]\npackage=false\n')
        (directory / 'uv.lock').write_text('version=1\nrevision=3\nrequires-python=' + json.dumps(python_requires()) + '\n'
            '[[package]]\nname="replacement-input"\nversion="0"\nsource={virtual="."}\n')
    real_path = Path
    monkeypatch.setattr(module, 'Path', lambda value: root if str(value) == '/usr/share/docassemble/managed/bundles' else real_path(value))
    monkeypatch.setattr(module.ExecutorPaths, 'from_env', lambda: SimpleNamespace(runtime_venv=tmp_path / 'runtime'))
    monkeypatch.setattr(sys, 'argv', ['replacement-image-build', '1.10.16'])
    return module, root


@pytest.mark.parametrize('fault', ['old-project', 'old-lock', 'missing-project', 'missing-lock'])
def test_replacement_inherited_pair_refuses_before_any_rewrite_or_lock_deletion(tmp_path, monkeypatch, fault):
    from unittest.mock import Mock
    module, root = replacement_builder(tmp_path, monkeypatch)
    # Invalid second input must not allow even first-bundle writes.
    path = root / 'runtime-r1' / ('pyproject.toml' if fault.endswith('project') else 'uv.lock')
    if fault.startswith('missing'):
        path.unlink()
    else:
        from docassemble.base.managed_python import python_requires
        path.write_text(path.read_text().replace(python_requires(), '==3.12.*'))
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    run = Mock(side_effect=AssertionError('foreign inherited pair cannot solve/sync'))
    monkeypatch.setattr(module.subprocess, 'run', run)
    with pytest.raises(AssertionError, match='retained project and lock Python target mismatch'):
        module.main()
    run.assert_not_called()
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('fault', ['current', 'old', 'missing'])
def test_replacement_generated_pair_checked_before_frozen_sync(tmp_path, monkeypatch, fault):
    import json
    from docassemble.base.managed_python import python_requires
    module, root = replacement_builder(tmp_path, monkeypatch)
    calls = []
    class ReachedSync(Exception):
        pass
    def run(arguments, **kwargs):
        calls.append(arguments[1])
        if arguments[1] == 'lock':
            lock = kwargs['cwd'] / 'uv.lock'
            lock.write_text('version=1\nrevision=3\n' + ('' if fault == 'missing' else
                'requires-python=' + json.dumps(python_requires() if fault == 'current' else '==3.12.*') + '\n'))
        else:
            assert arguments[1:6] == ['sync', '--frozen', '--offline', '--no-build', '--no-config']
            raise ReachedSync()
    monkeypatch.setattr(module.subprocess, 'run', run)
    if fault == 'current':
        with pytest.raises(ReachedSync):
            module.main()
        assert calls == ['lock', 'sync']
    else:
        with pytest.raises(AssertionError, match='retained project and lock Python target mismatch'):
            module.main()
        assert calls == ['lock']


def test_primary_bootstrap_target_check_imports_in_fixture_copy_layout(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    image = tmp_path / 'image'
    dockerfile = (ROOT / 'uv-experiment/fixture/Dockerfile').read_text()
    for line in dockerfile.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[0] != 'COPY':
            continue
        _, source, destination = parts
        if source not in ('uv-experiment/executor', 'docassemble_base/docassemble/base/managed_python.py'):
            continue
        target = image / destination.lstrip('/')
        if (ROOT / source).is_dir():
            shutil.copytree(ROOT / source, target, ignore=shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache'))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / source, target)
    script = (ROOT / 'uv-experiment/fixture/image-build.sh').read_text()
    helper = script.split('check_python_target() {', 1)[1].split('\n}\n', 1)[0]
    helper = 'check_python_target() {' + helper + '\n}\n'
    helper = helper.replace("'/opt/docassemble-executor/src'", repr(str(image / 'opt/docassemble-executor/src')))
    helper = helper.replace("'/docassemble_base'", repr(str(image / 'docassemble_base')))
    helper = helper.replace('"$UV_PYTHON" - ', '"$UV_PYTHON" -I - ')
    result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + helper + 'check_python_target "$INPUT_BUNDLE"'],
        capture_output=True, text=True, env=dict(os.environ, UV_PYTHON=sys.executable,
            INPUT_BUNDLE=str(image / 'opt/docassemble-executor')))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('fault', ['none', 'source-hash', 'metadata', 'duplicates', 'parser-defect', 'missing', 'duplicate'])
def test_materialize_wheels_verified_download_build_association(tmp_path, monkeypatch, fault):
    import importlib.util
    import io
    import json
    import tomli_w
    from types import SimpleNamespace
    from registry_wheel_fixture import METADATA_CASES, origin, wheel
    spec = importlib.util.spec_from_file_location('materialize_wheels', ROOT / 'uv-experiment/fixture/materialize_wheels.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    source = origin()
    (bundle / 'pyproject.toml').write_text('[project]\nrequires-python="==3.14.*"')
    (bundle / 'uv.lock').write_text(tomli_w.dumps({'requires-python': '==3.14.*', 'package': [source]}))
    class Download(io.BytesIO):
        def geturl(self):
            return source['sdist']['url']
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *a, **k: Download(b'bad' if fault == 'source-hash' else b'original sdist'))
    calls = []
    monkeypatch.setenv('UV_CACHE_DIR', '/ambient-cache-must-not-be-used')
    def run(command, **kwargs):
        if command == ['uv', '--version']:
            return SimpleNamespace(stdout=b'uv 0.12.21')
        calls.append(command)
        assert module.IMAGE_BUILD_TIMEOUT == 3600
        assert kwargs['timeout'] == module.IMAGE_BUILD_TIMEOUT and '--no-config' in command
        assert kwargs['env']['UV_PYTHON_DOWNLOADS'] == 'never'
        assert not Path(kwargs['env']['UV_CACHE_DIR']).exists()
        assert kwargs['env']['UV_CACHE_DIR'] != '/ambient-cache-must-not-be-used'
        output = Path(command[command.index('--out-dir') + 1])
        wheel(output, metadata_version='9.0' if fault == 'metadata' else None,
              metadata_bytes=METADATA_CASES.get(fault))
        if fault == 'duplicates':
            wheel(output, version='9.0')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', run)
    if fault == 'none':
        module.build_registry_sdists(bundle, tmp_path / 'built')
        record = json.loads((tmp_path / 'built/source-artifacts.json').read_text())
        assert len(calls) == len(record) == 1
        assert next(iter(record.values()))['sdist'] == source['sdist']
        assert len(next(iter(record.values()))['wheel_hash']) == 64
    else:
        with pytest.raises((ValueError, __import__('docassemble_executor.preparation', fromlist=['PreparationError']).PreparationError)):
            module.build_registry_sdists(bundle, tmp_path / 'built')
        assert not (tmp_path / 'built/source-artifacts.json').exists()
        if fault == 'source-hash':
            assert not calls


def test_materialize_wheels_published_abi3_and_universal_are_not_built(tmp_path):
    import importlib.util
    import tomli_w
    from registry_wheel_fixture import origin
    spec = importlib.util.spec_from_file_location('materialize_wheels', ROOT / 'uv-experiment/fixture/materialize_wheels.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'pyproject.toml').write_text('[project]\nrequires-python="==3.14.*"')
    from packaging.tags import sys_tags
    abi = next(tag for tag in sys_tags() if tag.interpreter == 'cp37' and tag.abi == 'abi3')
    packages = []
    for name, tag in [('crypto', str(abi)), ('universal', 'py3-none-any')]:
        row = origin(name, '1.0')
        row['wheels'] = [{'url': 'https://files.pythonhosted.org/' + name + '-1.0-' + tag + '.whl'}]
        packages.append(row)
    (tmp_path / 'uv.lock').write_text(tomli_w.dumps({'requires-python': '==3.14.*', 'package': packages}))
    assert module.eligible_sdists(tmp_path) == {}


@pytest.mark.parametrize('fault', ['missing', 'duplicate-json', 'duplicate-name', 'hash', 'foreign-tag'])
def test_materialize_wheels_sharing_never_chooses_arbitrary_cache_candidate(tmp_path, fault):
    import importlib.util
    import json
    import tomli_w
    from registry_wheel_fixture import reusable, wheel
    from docassemble_executor.preparation import PreparationError
    spec = importlib.util.spec_from_file_location('materialize_wheels', ROOT / 'uv-experiment/fixture/materialize_wheels.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    artifact = reusable(tmp_path / 'built')
    origin = dict(artifact.origin, wheel_hash=artifact.wheel_hash)
    record = {artifact.path.name: origin}
    if fault == 'missing':
        record = {}
    elif fault == 'duplicate-name':
        record['numpy-2.2.6-1-py3-none-any.whl'] = origin
    elif fault == 'hash':
        record[artifact.path.name]['wheel_hash'] = 'b' * 64
    elif fault == 'foreign-tag':
        artifact.path.unlink()
        foreign = wheel(artifact.path.parent, tag='cp312-cp312-win_amd64')
        from docassemble_executor.bundles import sha256_file
        record = {foreign.name: dict(origin, wheel_hash=sha256_file(foreign))}
    text = json.dumps(record)
    if fault == 'duplicate-json':
        text = '{' + json.dumps(artifact.path.name) + ':' + json.dumps(origin) + ',' + json.dumps(artifact.path.name) + ':' + json.dumps(origin) + '}'
    (artifact.path.parent / 'source-artifacts.json').write_text(text)
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    project = '[project]\nrequires-python="==3.14.*"\ndependencies = []\n[tool.uv.sources]\n'
    (bundle / 'pyproject.toml').write_text(project)
    (bundle / 'uv.lock').write_text(tomli_w.dumps({'requires-python': '==3.14.*', 'package': [artifact.origin]}))
    with pytest.raises((ValueError, PreparationError)):
        module.retain_built(bundle, artifact.path.parent)
    assert (bundle / 'pyproject.toml').read_text() == project


def test_materialize_wheels_image_build_order_attests_before_frozen_variants():
    script = (ROOT / 'uv-experiment/fixture/image-build.sh').read_text()
    assert script.index('--build-registry-sdists') < script.index('--retain-built') < script.index('uv sync --frozen --project "$BUNDLE_ROOT/core-r1"')
    assert 'REGISTRY_BUILDS=$(mktemp -d)' in script
    assert '--retain-built "$REGISTRY_BUILDS/wheels"' in script
    assert 'uv sync --frozen --no-build --project "$BUNDLE_ROOT/$name"' in script
    assert 'sdists-v9' not in (ROOT / 'uv-experiment/fixture/materialize_wheels.py').read_text()


def test_materialize_wheels_attested_build_variants_real_frozen_no_build(tmp_path, monkeypatch):
    """Dependency-free sdist models one build and two cold no-build consumers."""
    import importlib.util
    import io
    import json
    import os
    import shutil
    import subprocess
    import tarfile
    import tomli_w
    from registry_wheel_fixture import origin, wheel
    from docassemble_executor.bundles import sha256_file
    from docassemble_executor.registry_builder import RegistryBuilder
    from docassemble_executor.inventory import inspect_environment
    binary = shutil.which(os.environ.get('UV_BINARY', 'uv'))
    assert subprocess.check_output([binary, '--version'], text=True).split()[:2] == ['uv', '0.12.21']
    spec = importlib.util.spec_from_file_location('materialize_wheels', ROOT / 'uv-experiment/fixture/materialize_wheels.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture_wheel = wheel(tmp_path / 'fixture')
    # No backend dependency downloads and no compiler; the real uv build still
    # has to consume the exact verified sdist and produce/validate the artifact.
    backend = ('import pathlib\ndef build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n'
        '    p=pathlib.Path(wheel_directory)/' + repr(fixture_wheel.name) + '\n'
        '    p.write_bytes(' + repr(fixture_wheel.read_bytes()) + ')\n    return p.name\n')
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        for filename, content in {'pyproject.toml': '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n',
                                   'backend.py': backend}.items():
            encoded = content.encode()
            info = tarfile.TarInfo('numpy-2.2.6/' + filename)
            info.size = len(encoded)
            archive.addfile(info, io.BytesIO(encoded))
    source = origin(data=data.getvalue())
    class Download(io.BytesIO):
        def geturl(self):
            return source['sdist']['url']
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *a, **k: Download(data.getvalue()))
    project_text = '[project]\nname="variant"\nversion="0"\nrequires-python="==3.14.*"\ndependencies = ["numpy==2.2.6"]\n[tool.uv]\npackage=false\n[tool.uv.sources]\n'
    bundles = [tmp_path / 'core', tmp_path / 'variant']
    for bundle in bundles:
        bundle.mkdir()
        (bundle / 'pyproject.toml').write_text(project_text)
        (bundle / 'uv.lock').write_text(tomli_w.dumps({'requires-python': '==3.14.*', 'package': [source]}))
    output = tmp_path / 'attested'
    module.build_registry_sdists(bundles[0], output)
    origins = json.loads((output / 'source-artifacts.json').read_text())
    assert origins[fixture_wheel.name]['wheel_hash'] == sha256_file(output / fixture_wheel.name)
    for bundle in bundles:
        module.retain_built(bundle, output)
    shutil.rmtree(output)
    builder = RegistryBuilder(tmp_path / 'store', binary)
    for bundle in bundles:
        (bundle / 'uv.lock').unlink()  # Replace the minimal test source document with a real uv apply lock.
        cache = bundle / 'cache'
        venv = bundle / 'venv'
        builder._run(['lock', '--offline', '--no-config'], bundle, cache, venv)
        # No original URL remains in the apply lock; no producer/cache authority
        # is needed: the variant's explicit local wheel is the frozen source.
        assert 'files.pythonhosted.org' not in (bundle / 'uv.lock').read_text()
        lock = tomllib.loads((bundle / 'uv.lock').read_text())
        assert next(row['source'] for row in lock['package'] if row['name'] == 'numpy') == {
            'path': 'wheels/' + fixture_wheel.name}
        assert sha256_file(bundle / 'wheels' / fixture_wheel.name) == origins[fixture_wheel.name]['wheel_hash']
        assert not output.exists()
        shutil.rmtree(cache)
        builder._run(['sync', '--frozen', '--no-build', '--offline', '--no-config'], bundle, cache, venv)
        assert inspect_environment(venv) == {'numpy': '2.2.6'}
        from docassemble_executor.inventory import verify_environment, wheel_payload_material
        assert verify_environment(venv, {'numpy': '2.2.6'}, wheel_payload_material(bundle / 'wheels')).ok


@pytest.mark.parametrize('expires', [False, True], ids=['headroom', 'redacted-expiry'])
def test_materialize_wheels_image_build_timeout_is_separate_from_runtime_bound(tmp_path, monkeypatch, expires):
    import importlib.util
    import io
    import subprocess
    import tomli_w
    from types import SimpleNamespace
    from registry_wheel_fixture import origin, wheel
    from docassemble_executor.preparation import PreparationError
    from docassemble_executor.registry_builder import RegistryBuilder
    spec = importlib.util.spec_from_file_location('materialize_wheels', ROOT / 'uv-experiment/fixture/materialize_wheels.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    source = origin()
    (bundle / 'pyproject.toml').write_text('[project]\nrequires-python="==3.14.*"')
    (bundle / 'uv.lock').write_text(tomli_w.dumps({'requires-python': '==3.14.*', 'package': [source]}))
    before = (bundle / 'pyproject.toml').read_bytes(), (bundle / 'uv.lock').read_bytes()
    class Download(io.BytesIO):
        def geturl(self):
            return source['sdist']['url']
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *a, **k: Download(b'original sdist'))
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs['timeout']))
        if command[:2] == ['uv', '--version']:
            assert kwargs['timeout'] == 600
            return SimpleNamespace(stdout=b'uv 0.12.21')
        if command[1] == 'sync':
            assert kwargs['timeout'] == 600  # Actual runtime _run, not source text.
            return SimpleNamespace(returncode=0)
        assert command[:3] == ['uv', 'build', '--wheel']
        assert module.IMAGE_BUILD_TIMEOUT == 3600
        assert kwargs['timeout'] == module.IMAGE_BUILD_TIMEOUT
        assert '--no-config' in command and kwargs['env']['UV_PYTHON_DOWNLOADS'] == 'never'
        output = Path(command[command.index('--out-dir') + 1])
        wheel(output)  # Even a partial output must never be attested after expiry.
        if expires:
            raise subprocess.TimeoutExpired(['uv', 'SECRET-COMMAND'], kwargs['timeout'],
                                            output=b'SECRET-OUTPUT', stderr=b'SECRET-ERROR')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', run)
    RegistryBuilder(tmp_path / 'store')._run(['sync', '--frozen', '--no-config'], bundle,
                                           tmp_path / 'cache', tmp_path / 'venv')
    output = tmp_path / 'attested'
    if expires:
        with pytest.raises(PreparationError) as error:
            module.build_registry_sdists(bundle, output)
        assert str(error.value) == 'image registry wheel build timed out'
        assert error.value.__suppress_context__ and error.value.__cause__ is None
        assert 'SECRET' not in str(error.value)
        assert not (output / 'source-artifacts.json').exists()
        assert not list(output.glob('*.whl'))
    else:
        module.build_registry_sdists(bundle, output)
        assert (output / 'source-artifacts.json').exists()
    assert sum(command[1] == 'build' for command, timeout in calls) == 1
    assert ((bundle / 'pyproject.toml').read_bytes(), (bundle / 'uv.lock').read_bytes()) == before
