"""Detached helper diagnostic pass-through and capture checks; host-only, no app imports."""
import ast
import base64
from concurrent.futures import Future
from datetime import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

EXPERIMENT = Path(__file__).resolve().parents[2]
HELPER = EXPERIMENT / 'fixture/sentinel-user/docassemble/sentinel_fixture/helper_work.py'
HOOKS = EXPERIMENT / 'fixture/helper_probe_hooks.py'
SERVICE = EXPERIMENT / 'fixture/service_probe_hooks.py'
SCENARIO = EXPERIMENT / 'scenarios/helpers_mail.py'


def load_source(path):
    spec = importlib.util.spec_from_file_location('diagnostic_fixture', path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_text(), str(path), 'exec'), module.__dict__)
    return module


@pytest.mark.parametrize('path', [HELPER, HOOKS, SERVICE, SCENARIO, Path(__file__)])
def test_diagnostic_sources_and_embedded_scripts_compile_without_bytecode(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    compile(tree, str(path), 'exec')
    if path == SCENARIO:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in
                    ('child_script', 'fd_script', 'listing_script', 'log_script', 'artifact_script') for target in node.targets):
                compile(ast.literal_eval(node.value), 'helpers_mail embedded diagnostic', 'exec')


def helper_fixture(tmp_path, monkeypatch):
    module = load_source(HELPER)
    module.ROOT = tmp_path
    monkeypatch.setenv('DA_HELPER_PROOF_LABEL', 'detached-helper')
    monkeypatch.setenv('DA_HELPER_PROOF_DETACHED', 'true')
    monkeypatch.setenv('DA_HELPER_PROOF_HOLD', 'true')
    monkeypatch.setenv('DA_HELPER_PROOF_NESTED', 'true')
    monkeypatch.setenv('DA_ACCEPTED_CALLER', 'non-hex-secret')
    (tmp_path / 'detached-helper.release').touch()
    monkeypatch.setattr(os, 'fork', Mock(return_value=0))
    monkeypatch.setattr(os, 'setsid', Mock())
    monkeypatch.setattr(os, 'closerange', Mock())
    monkeypatch.setattr(os, '_exit', Mock())
    monkeypatch.setattr(sys, 'stderr', io.StringIO())
    return module


def test_helper_child_trace_schema_redaction_and_byte_exact_markers(tmp_path, monkeypatch):
    module = helper_fixture(tmp_path, monkeypatch)
    monkeypatch.delitem(sys.modules, 'docassemble_executor.capabilities', raising=False)
    nested = Mock(return_value={'result': ['non-hex-secret', 'a' * 64]})
    monkeypatch.setitem(sys.modules, 'service_probe_hooks', SimpleNamespace(nested=nested, __file__='stub-service.py'))
    module.__spec__.loader_state = {'installed_origin': 'installed-helper.py'}
    module.record_work('monthly')
    os.closerange.assert_called_once_with(3, 1024)
    os.setsid.assert_called_once_with()
    os._exit.assert_called_once_with(0)
    records = [json.loads(line) for line in (tmp_path / 'detached-helper.trace.jsonl').read_text().splitlines()]
    assert [record['phase'] for record in records] == [
        'fd-closed', 'started', 'released', 'nested-import-enter', 'nested-import-return',
        'nested-enter', 'nested-return', 'completion-write-enter', 'completion-write-return', 'exit-enter']
    assert records[0]['details'] == {'helper_module_file': str(HELPER), 'installed_origin': 'installed-helper.py'}
    assert records[6]['details'] == {'result': {'result': ['[redacted-capability]', '[redacted-capability]']}}
    for record in records:
        assert set(record) == {'v', 'utc', 'monotonic_ns', 'pid', 'ppid', 'pgid', 'sid', 'uid',
                               'label', 'kind', 'phase', 'capability_present', 'current_capability_present', 'details'}
        assert datetime.fromisoformat(record['utc']).utcoffset().total_seconds() == 0
        assert record['monotonic_ns'] > 0 and record['pid'] == os.getpid()
        assert record['capability_present'] is True and record['current_capability_present'] is None
    assert 'docassemble_executor.capabilities' not in sys.modules
    assert (tmp_path / 'detached-helper.started').read_bytes() == str(os.getpid()).encode()
    assert (tmp_path / 'detached-helper.completed').read_bytes() == json.dumps({'kind': 'monthly', 'uid': os.getuid()}).encode()
    assert not (tmp_path / 'detached-helper.traceback.txt').exists()
    assert module._redact('x' + 'b' * 64 + 'x') == 'x' + 'b' * 64 + 'x'


def test_helper_nested_exception_propagates_with_independent_redacted_traceback(tmp_path, monkeypatch):
    module = helper_fixture(tmp_path, monkeypatch)
    error = RuntimeError('non-hex-secret ' + 'f' * 64)
    nested = Mock(side_effect=error)
    monkeypatch.setitem(sys.modules, 'service_probe_hooks', SimpleNamespace(nested=nested, __file__='stub-service.py'))
    monkeypatch.setitem(sys.modules, 'docassemble_executor.capabilities', SimpleNamespace(current=lambda: 'present'))
    with pytest.raises(RuntimeError) as caught:
        module.record_work('monthly')
    assert caught.value is error
    os._exit.assert_not_called()
    assert not (tmp_path / 'detached-helper.completed').exists()
    text = (tmp_path / 'detached-helper.traceback.txt').read_text()
    assert 'RuntimeError: [redacted-capability] [redacted-capability]' in text
    assert 'non-hex-secret' not in text and 'f' * 64 not in text
    records = [json.loads(line) for line in (tmp_path / 'detached-helper.trace.jsonl').read_text().splitlines()]
    assert records[-1]['phase'] == 'exception'
    assert records[-1]['details'] == {'type': 'RuntimeError', 'message': '[redacted-capability] [redacted-capability]'}
    assert records[-1]['current_capability_present'] is True
    assert 'nested-return' not in [record['phase'] for record in records]


def test_helper_stderr_forwards_original_returns_and_errors(tmp_path, monkeypatch):
    module = helper_fixture(tmp_path, monkeypatch)
    original = SimpleNamespace(write=Mock(return_value=42), flush=Mock(return_value=17), sentinel='forwarded')
    monkeypatch.setattr(sys, 'stderr', original)
    monkeypatch.setitem(sys.modules, 'service_probe_hooks', SimpleNamespace(nested=Mock(return_value='cats'), __file__='stub'))
    module.record_work('monthly')
    assert sys.stderr.sentinel == 'forwarded'
    assert sys.stderr.write('non-hex-secret') == 42
    original.write.assert_called_once_with('non-hex-secret')
    assert sys.stderr.flush() == 17
    error = BrokenPipeError('non-hex-secret')
    original.write.side_effect = error
    with pytest.raises(BrokenPipeError) as caught:
        sys.stderr.write('second')
    assert caught.value is error
    original.flush.side_effect = error
    with pytest.raises(BrokenPipeError) as caught:
        sys.stderr.flush()
    assert caught.value is error
    assert (tmp_path / 'detached-helper.stderr.txt').read_text() == '[redacted-capability]second'
    phases = [json.loads(line)['phase'] for line in (tmp_path / 'detached-helper.trace.jsonl').read_text().splitlines()]
    assert phases[-2:] == ['stderr-write-error', 'stderr-flush-error']


def test_helper_parent_and_ordinary_callers_preserve_markers_without_trace(tmp_path, monkeypatch):
    module = helper_fixture(tmp_path, monkeypatch)
    os.fork.return_value = 123
    module.record_work('monthly')
    assert (tmp_path / 'detached-helper.parent-exited').read_bytes() == b''
    os.setsid.assert_not_called()
    os.closerange.assert_not_called()
    assert not (tmp_path / 'detached-helper.trace.jsonl').exists()
    monkeypatch.setenv('DA_HELPER_PROOF_DETACHED', 'false')
    nested = Mock(return_value='cats')
    monkeypatch.setitem(sys.modules, 'service_probe_hooks', SimpleNamespace(nested=nested))
    module.record_work('monthly')
    nested.assert_called_once_with('detached-helper')
    assert (tmp_path / 'detached-helper.started').read_bytes() == str(os.getpid()).encode()
    assert (tmp_path / 'detached-helper.completed').read_bytes() == json.dumps({'kind': 'monthly', 'uid': os.getuid()}).encode()
    assert not (tmp_path / 'detached-helper.trace.jsonl').exists()


def load_hooks(tmp_path, monkeypatch, *, detached='true', label='detached-helper', argv='/executor/authority.py', overlay='0'):
    monkeypatch.setenv('DA_HELPER_PROOF_DETACHED', detached)
    monkeypatch.setenv('DA_HELPER_PROOF_LABEL', label)
    monkeypatch.setenv('DA_HELPER_PROOF_SOURCE_OVERLAY', overlay)
    monkeypatch.setattr(sys, 'argv', [argv])
    monkeypatch.setattr(sys, 'setprofile', Mock())
    monkeypatch.setattr(sys, 'meta_path', list(sys.meta_path))
    monkeypatch.setitem(sys.modules, 'service_probe_hooks', SimpleNamespace())
    source = HOOKS.read_text().replace("Path('/tmp/da-helper-proof')", 'Path(' + repr(str(tmp_path)) + ')')
    namespace = {}
    exec(compile(source, str(HOOKS), 'exec'), namespace)
    return namespace


@pytest.mark.parametrize('status,exited,exit_code,signalled,signal', [(0, True, 0, False, None), (9, False, None, True, 9)])
def test_guardian_waitpid_one_original_call_unchanged_tuple_and_decoded_json(tmp_path, monkeypatch, status, exited, exit_code, signalled, signal):
    result = (123, status)
    original = Mock(return_value=result)
    monkeypatch.setattr(os, 'waitpid', original)
    load_hooks(tmp_path, monkeypatch)
    assert os.waitpid(-1, 0) is result
    original.assert_called_once_with(-1, 0)
    records = [json.loads(line) for line in (tmp_path / 'detached-helper.guardian-reaps.jsonl').read_text().splitlines()]
    assert records[0]['event'] == 'installed' and records[0]['guardian_starttime'] > 0
    assert records[1] == dict(records[1], requested_pid=-1, options=0, reaped_pid=123, raw_status=status,
                              exited=exited, exit_code=exit_code, signalled=signalled, signal=signal)
    assert set(records[1]) == {'v', 'utc', 'monotonic_ns', 'guardian_pid', 'requested_pid', 'options',
                               'reaped_pid', 'raw_status', 'exited', 'exit_code', 'signalled', 'signal', 'core_dumped'}


def test_guardian_waitpid_original_exception_and_diagnostic_io_failure_do_not_change_result(tmp_path, monkeypatch):
    error = ChildProcessError('original ECHILD')
    original = Mock(side_effect=error)
    monkeypatch.setattr(os, 'waitpid', original)
    load_hooks(tmp_path, monkeypatch)
    before = (tmp_path / 'detached-helper.guardian-reaps.jsonl').read_bytes()
    with pytest.raises(ChildProcessError) as caught:
        os.waitpid(-1, 0)
    assert caught.value is error
    original.assert_called_once_with(-1, 0)
    assert (tmp_path / 'detached-helper.guardian-reaps.jsonl').read_bytes() == before
    original.side_effect = None
    original.return_value = (123, 0)
    monkeypatch.setattr(Path, 'open', Mock(side_effect=OSError('diagnostic denied')))
    assert os.waitpid(-1, 0) is original.return_value
    assert original.call_count == 2


@pytest.mark.parametrize('detached,label,argv', [('false', 'detached-helper', '/executor/authority.py'),
                                               ('true', 'other', '/executor/authority.py'),
                                               ('true', 'detached-helper', '/executor/helper_runtime.py')])
def test_guardian_gate_off_has_no_waitpid_or_file_side_effects(tmp_path, monkeypatch, detached, label, argv):
    original = Mock(return_value=(123, 0))
    monkeypatch.setattr(os, 'waitpid', original)
    load_hooks(tmp_path, monkeypatch, detached=detached, label=label, argv=argv)
    assert os.waitpid is original
    original.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_lazy_overlay_only_resolves_canonical_installed_spec(tmp_path, monkeypatch):
    original = Mock()
    monkeypatch.setattr(os, 'waitpid', original)
    finder = load_hooks(tmp_path, monkeypatch, overlay='1', argv='/executor/helper_runtime.py')['HelperSourceOverlay']
    assert isinstance(sys.meta_path[0], finder)
    installed = Mock(return_value=SimpleNamespace(origin='/installed/helper_work.py'))
    monkeypatch.setattr(importlib.machinery.PathFinder, 'find_spec', installed)
    assert sys.meta_path[0].find_spec('unrelated', ['installed']) is None
    installed.assert_not_called()
    spec = sys.meta_path[0].find_spec('docassemble.sentinel_fixture.helper_work', ['installed'])
    installed.assert_called_once_with('docassemble.sentinel_fixture.helper_work', ['installed'])
    assert spec.origin == '/opt/docassemble-fixture/sentinel-user/docassemble/sentinel_fixture/helper_work.py'
    assert spec.loader_state == {'installed_origin': '/installed/helper_work.py'}
    installed.return_value = None
    with pytest.raises(ImportError):
        sys.meta_path[0].find_spec('docassemble.sentinel_fixture.helper_work', ['installed'])


@pytest.mark.parametrize('detached,overlay', [('false', '1'), ('true', '0')])
def test_overlay_gate_off_does_not_install_finder(tmp_path, monkeypatch, detached, overlay):
    before = list(sys.meta_path)
    load_hooks(tmp_path, monkeypatch, detached=detached, overlay=overlay, argv='/executor/helper_runtime.py')
    assert sys.meta_path == before


def service_stubs(monkeypatch, tmp_path):
    config = SimpleNamespace(daconfig={'loaded': True})
    plural = Mock(return_value='cats')
    def convert(source, document, input_format, output_format):
        Path(document).write_bytes(b'PK real stub document')
        return True
    conversion = Mock(side_effect=convert)
    pandoc = SimpleNamespace(convert_file=conversion)
    managed = SimpleNamespace(conversion_environment=Mock(return_value={'UNCHANGED': 'env'}),
                              conversion_path=Mock(return_value='/original/unoconvert'))
    base = SimpleNamespace(config=config, pattern=SimpleNamespace(pattern_en=SimpleNamespace(pluralize=plural)))
    for name, value in {'docassemble': SimpleNamespace(base=base), 'docassemble.base': base,
                        'docassemble.base.config': config, 'docassemble.base.pandoc': pandoc,
                        'docassemble.base.managed_services': managed}.items():
        monkeypatch.setitem(sys.modules, name, value)
    wire = Mock(return_value={'result': 'cats'})
    monkeypatch.setitem(sys.modules, 'service_client', SimpleNamespace(nltk=wire))
    def uno(argv, **kwargs):
        assert argv == ['/original/unoconvert', str(tmp_path / 'detached-helper.docx'), str(tmp_path / 'detached-helper.pdf')]
        assert kwargs == {'env': {'UNCHANGED': 'env'}, 'capture_output': True, 'timeout': 90}
        Path(argv[-1]).write_bytes(b'%PDF real stub conversion')
        return subprocess.CompletedProcess(argv, 0, b'output\xff', b'errors\xff')
    operation = Mock(side_effect=uno)
    monkeypatch.setattr(subprocess, 'run', operation)
    module = load_source(SERVICE)
    module.ROOT = tmp_path
    return module, wire, plural, conversion, operation


def test_nested_callbacks_exact_order_and_single_operations(tmp_path, monkeypatch):
    module, wire, plural, conversion, operation = service_stubs(monkeypatch, tmp_path)
    records = []
    assert module.nested('detached-helper', trace=lambda phase, **details: records.append((phase, details))) == 'cats'
    pairs = ['config-import', 'wire-nlp', 'pattern', 'source-write', 'convert-file', 'docx-check',
             'unoconvert', 'pdf-check', 'service-completion-write']
    assert [phase for phase, _ in records] == [name + '-' + suffix for name in pairs for suffix in ('enter', 'return')]
    assert records[3][1] == {'reply': {'result': 'cats'}}
    assert records[13][1] == {'returncode': 0, 'stdout': 'output\ufffd', 'stderr': 'errors\ufffd'}
    wire.assert_called_once_with('observe:detached-helper')
    plural.assert_called_once_with('cat')
    assert conversion.call_count == operation.call_count == 1
    assert (tmp_path / 'detached-helper.completed').read_bytes() == ('cats:' + str(os.getuid())).encode()


def test_nested_callback_exception_and_wire_reply_indexing_propagate(tmp_path, monkeypatch):
    module, wire, plural, conversion, operation = service_stubs(monkeypatch, tmp_path)
    error = RuntimeError('callback failure')
    def callback(phase, **details):
        if phase == 'wire-nlp-return':
            raise error
    with pytest.raises(RuntimeError) as caught:
        module.nested('detached-helper', trace=callback)
    assert caught.value is error
    wire.assert_called_once()
    plural.assert_not_called()
    conversion.assert_not_called()
    operation.assert_not_called()
    wire.return_value = {'error': 'denied'}
    records = []
    with pytest.raises(KeyError):
        module.nested('detached-helper', trace=lambda phase, **details: records.append((phase, details)))
    assert records[-1] == ('wire-nlp-return', {'reply': {'error': 'denied'}})


def scenario_diagnostics(tmp_path, monkeypatch, docker):
    monkeypatch.syspath_prepend(str(EXPERIMENT / 'scenarios'))
    scenario = load_source(SCENARIO)
    main = next(node for node in ast.parse(SCENARIO.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    first = next(i for i, node in enumerate(main.body) if isinstance(node, ast.Assign) and
                 any(isinstance(target, ast.Name) and target.id == 'repo' for target in node.targets))
    last = next(i for i, node in enumerate(main.body) if isinstance(node, ast.ClassDef) and node.name == 'Pool')
    names = {'utc', 'redact', 'capture_error', 'diagnostic', 'diagnostic_exec', 'save_exec_result',
             'register_exec', 'event_record', 'start_events', 'stop_events', 'detached_snapshot', 'detached_export', 'detached_failure'}
    body = main.body[first:last] + [node for node in main.body if isinstance(node, ast.FunctionDef) and node.name in names]
    function = ast.parse('def diagnostics(root, docker, evidence):\n    return locals()').body[0]
    function.body = body + function.body
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(SCENARIO), 'exec'), scenario.__dict__)
    return scenario, scenario.diagnostics(tmp_path, docker, scenario.Evidence(tmp_path))


def test_exec_results_partial_timeout_redaction_and_event_structural_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('DA_ACCEPTED_CALLER', 'non-hex-secret')
    _, functions = scenario_diagnostics(tmp_path, monkeypatch, SimpleNamespace())
    error = subprocess.TimeoutExpired(['not saved'], 900, output=b'non-hex-secret', stderr=('a' * 64).encode())
    functions['save_exec_result']('helper-exec', error)
    assert (tmp_path / 'detached-debug/helper-exec.stdout').read_text() == '[redacted-capability]'
    assert (tmp_path / 'detached-debug/helper-exec.stderr').read_text() == '[redacted-capability]'
    metadata = json.loads((tmp_path / 'detached-debug/helper-exec.json').read_text())
    assert metadata['returncode'] is None and metadata['timeout'] == 900
    assert metadata['exception_type'] == 'TimeoutExpired'
    future = Future()
    functions['register_exec']('controller-exec', future)
    future.set_result(subprocess.CompletedProcess([], 137, 'eventual stdout', 'eventual stderr'))
    assert (tmp_path / 'detached-debug/controller-exec.stdout').read_text() == 'eventual stdout'
    record = json.loads(functions['event_record'](json.dumps({
        'id': 'b' * 64, 'timeNano': 123, 'Actor': {'ID': 'c' * 64,
        'Attributes': {'execCommand': ['non-hex-secret', 'a' * 64]}}, 'Action': 'exec_die'})))
    assert record['id'] == 'b' * 64 and record['Actor']['ID'] == 'c' * 64 and record['timeNano'] == 123
    assert record['Actor']['Attributes']['execCommand'] == ['[redacted-capability]', '[redacted-capability]']
    assert json.loads(functions['event_record']('bad JSON non-hex-secret')) == 'bad JSON [redacted-capability]'
    assert 'events JSON parse' in (tmp_path / 'detached-debug/capture-errors.jsonl').read_text()


def test_snapshot_stat_identity_binary_exports_and_independent_capture_errors(tmp_path, monkeypatch):
    helper_root = tmp_path / 'helper'
    helper_root.mkdir()
    (helper_root / 'detached-helper.started').write_text(str(os.getpid()))
    (helper_root / 'detached-helper.parent-exited').touch()
    class FakeDocker:
        def __init__(self):
            self.calls = []
        def exec(self, name, command, **kwargs):
            assert kwargs == {'timeout': 10, 'check_result': False}
            self.calls.append(command)
            if command[0] == 'ps':
                raise OSError('injected ps failure')
            if command[:2] == ['cat', '/sys/fs/cgroup/memory.events']:
                return subprocess.CompletedProcess([], 1, '', 'permission denied')
            code = command[2]
            if 'def proc_stat(pid)' in code:
                source = code.replace("Path('/tmp/da-helper-proof')", 'Path(' + repr(str(helper_root)) + ')')
                with monkeypatch.context() as context:
                    context.setattr(sys, 'argv', ['-c', command[3]])
                    context.setattr(sys, 'stdout', io.StringIO())
                    exec(compile(source, 'child snapshot', 'exec'), {})
                    text = sys.stdout.getvalue()
                return subprocess.CompletedProcess([], 0, text, '')
            if 'os.readlink(fd)' in code:
                assert '[capability-present]' in code and 'environ' not in code
                return subprocess.CompletedProcess([], 0, '123 0 pipe:[1]', '')
            if 'entries = []' in code:
                entries = [{'path': '/tmp/da-helper-proof/detached-helper.pdf', 'name': 'detached-helper.pdf', 'type': 'file'}]
                return subprocess.CompletedProcess([], 0, json.dumps(entries), '')
            if 'base64.b64encode(payload)' in code:
                return subprocess.CompletedProcess([], 0, json.dumps({
                    'payload': base64.b64encode(b'\x00\xffbinary').decode()}), '')
            if 'load_config' in code:
                return subprocess.CompletedProcess([], 0, '{"records": [], "config_error": null}', '')
            return subprocess.CompletedProcess([], 0, '{}', '')
        def run(self, command, **kwargs):
            assert kwargs == {'timeout': 10, 'check_result': False}
            self.calls.append(command)
            assert command[0] != 'cp', 'helper artifacts must not be raw-copied to the host'
            return subprocess.CompletedProcess([], 0, '', '')
    docker = FakeDocker()
    _, functions = scenario_diagnostics(tmp_path, monkeypatch, docker)
    # Set the closure's immutable ID using the actual start routine, without launching Docker.
    original_run = docker.run
    def run(command, **kwargs):
        if command[:2] == ['inspect', 'da-uv-helper-proof']:
            return subprocess.CompletedProcess([], 0, 'immutable-container-id', '')
        return original_run(command, **kwargs)
    docker.run = run
    monkeypatch.setattr(subprocess, 'Popen', Mock(side_effect=OSError('collector unavailable')))
    functions['diagnostic']('start events', functions['start_events'])
    functions['detached_snapshot']('01-started')
    first = json.loads((tmp_path / 'detached-debug/01-started/child.json').read_text())
    assert first['identity_matches'] is True and first['original_identity'] == [os.getpid(), first['starttime']]
    assert first['raw_stat'] and first['ancestors'] and first['absent'] is False
    functions['detached_snapshot']('04-before-release')
    later = json.loads((tmp_path / 'detached-debug/04-before-release/child.json').read_text())
    assert later['identity_matches'] is True
    assert (tmp_path / 'detached-debug/04-before-release/artifacts/helper/detached-helper.pdf').read_bytes() == b'\x00\xffbinary'
    errors = (tmp_path / 'detached-debug/capture-errors.jsonl').read_text()
    assert 'injected ps failure' in errors and 'permission denied' in errors
    assert (tmp_path / 'detached-debug/04-before-release/authority-fds.txt').exists()


def test_overlay_cold_setup_injects_exact_bytes_with_shell_quoted_script(tmp_path, monkeypatch):
    _, functions = scenario_diagnostics(tmp_path, monkeypatch, SimpleNamespace())
    main = next(node for node in ast.parse(SCENARIO.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    block = next(node for node in main.body if isinstance(node, ast.Try))
    setup = next(i for i, node in enumerate(block.body) if isinstance(node, ast.Assign) and
                 any(isinstance(target, ast.Name) and target.id == 'cold_setup' for target in node.targets))
    namespace = dict(functions, source_overlay=True, repo=EXPERIMENT.parent, Path=Path, shlex=shlex)
    exec(compile(ast.Module(body=block.body[setup:setup + 2], type_ignores=[]), str(SCENARIO), 'exec'), namespace)
    command = shlex.split(namespace['cold_setup'])
    assert command[:2] == ['/usr/share/docassemble/executor-venv/bin/python', '-c']
    script = ast.parse(command[2].removesuffix(';'))
    for node in ast.walk(script):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'Path'
                and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)):
            destination = node.args[0].value
            node.args[0].value = str(tmp_path / destination.lstrip('/'))
    exec(compile(script, 'cold setup overlay', 'exec'), {'Path': Path})
    for source, destination in functions['fixture_sources']:
        target = tmp_path / destination.lstrip('/')
        assert target.read_bytes() == (EXPERIMENT / source).read_bytes()
        assert target.stat().st_mode & 0o777 == 0o644
    assert namespace['cold_setup'].index('write_bytes') < namespace['cold_setup'].index('cp /opt/docassemble-fixture/helper_probe_hooks.py')


def test_detached_capture_schedule_and_cleanup_stay_inside_pool_failure_boundary():
    source = SCENARIO.read_text()
    start = source.index('        detached_active = True')
    end = source.index("        send('hold:mail-crash')", start)
    detached = source[start:end]
    for earlier, later in [
        ('01-started', 'before = uv_log()'),
        ("str(wrapper_pid('monthly'))", '02-after-wrapper-kill'),
        ('02-after-wrapper-kill', 'job.result(timeout=30)'),
        ('03-after-controller-kill', 'operation.result(timeout=30)'),
        ("http.status('/user/sign-in') == 503", '04-before-release'),
        ('04-before-release', "release('detached-helper')"),
        ("present('detached-helper.completed'), 120", '05-completed'),
        ('05-completed', "nested_receipts('detached-helper', mode='closed')"),
        ('06-success', 'detached_active = False'),
        ('except BaseException:\n                detached_failure()', 'finally:'),
    ]:
        assert detached.index(earlier) < detached.index(later)
    assert detached.index('try:') > detached.index('with Pool(')
    assert detached.index('ready()') < detached.index('except BaseException:\n                detached_failure()')
    assert 'release(' not in detached.split('finally:')[1]


def test_guardian_nonpositive_wait_result_is_not_logged(tmp_path, monkeypatch):
    original = Mock(return_value=(0, 0))
    monkeypatch.setattr(os, 'waitpid', original)
    load_hooks(tmp_path, monkeypatch)
    path = tmp_path / 'detached-helper.guardian-reaps.jsonl'
    before = path.read_bytes()
    assert os.waitpid(-1, 1) is original.return_value
    original.assert_called_once_with(-1, 1)
    assert path.read_bytes() == before


def test_child_exception_handler_begins_before_setsid_and_diagnostic_io_never_masks_error(tmp_path, monkeypatch):
    module = helper_fixture(tmp_path, monkeypatch)
    error = OSError('setsid failed non-hex-secret')
    os.setsid.side_effect = error
    with pytest.raises(OSError) as caught:
        module.record_work('monthly')
    assert caught.value is error
    os.closerange.assert_not_called()
    assert 'setsid failed [redacted-capability]' in (tmp_path / 'detached-helper.traceback.txt').read_text()
    assert not (tmp_path / 'detached-helper.started').exists()
    monkeypatch.setattr(Path, 'open', Mock(side_effect=OSError('all diagnostics denied')))
    with pytest.raises(OSError) as caught:
        module.record_work('monthly')
    assert caught.value is error


def test_nested_ordinary_caller_retains_marker_bytes_and_single_payload_calls(tmp_path, monkeypatch):
    module, wire, plural, conversion, operation = service_stubs(monkeypatch, tmp_path)
    assert module.nested('detached-helper') == 'cats'
    assert (tmp_path / 'detached-helper.rtf').read_bytes() == b'{\\rtf1\\ansi observe:detached-helper accepted nested conversion}'
    assert (tmp_path / 'detached-helper.completed').read_bytes() == ('cats:' + str(os.getuid())).encode()
    assert wire.call_count == plural.call_count == conversion.call_count == operation.call_count == 1


def test_event_collector_drains_redacted_pipes_and_only_stops_host_process(tmp_path, monkeypatch):
    class FakeDocker:
        def run(self, command, **kwargs):
            assert command == ['inspect', 'da-uv-helper-proof', '--format', '{{.Id}}']
            assert kwargs == {'timeout': 10, 'check_result': False}
            return subprocess.CompletedProcess([], 0, 'immutable-container-id', '')
    monkeypatch.setenv('DA_ACCEPTED_CALLER', 'non-hex-secret')
    collector = SimpleNamespace(stdout=io.StringIO('{"id":"' + 'd' * 64 + '","command":"non-hex-secret"}\n'),
                                stderr=io.StringIO('stderr non-hex-secret\n'), returncode=None)
    def terminate():
        collector.returncode = -15
    collector.terminate = Mock(side_effect=terminate)
    collector.wait = Mock(return_value=-15)
    collector.kill = Mock()
    popen = Mock(return_value=collector)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    _, functions = scenario_diagnostics(tmp_path, monkeypatch, FakeDocker())
    functions['start_events']()
    functions['stop_events']()
    argv = popen.call_args.args[0]
    assert argv[:2] == ['docker', 'events'] and 'container=immutable-container-id' in argv
    assert popen.call_args.kwargs['stdout'] == subprocess.PIPE and popen.call_args.kwargs['stderr'] == subprocess.PIPE
    assert json.loads((tmp_path / 'detached-debug/events.jsonl').read_text()) == {
        'id': 'd' * 64, 'command': '[redacted-capability]'}
    assert (tmp_path / 'detached-debug/events.stderr').read_text() == 'stderr [redacted-capability]\n'
    collector.terminate.assert_called_once_with()
    collector.wait.assert_called_once_with(timeout=10)
    collector.kill.assert_not_called()
    status = json.loads((tmp_path / 'detached-debug/events-collector.json').read_text())
    assert status['returncode'] == -15 and status['readers_drained'] is True
    assert status['start_utc'] and status['terminal_utc']


def test_helpers_mail_detached_export_redacts_before_first_host_write_and_preserves_binaries(tmp_path, monkeypatch):
    capability = 'caller-"quoted"\\value'
    monkeypatch.setenv('DA_ACCEPTED_CALLER', capability)
    text = (capability + ' ' + json.dumps(capability)[1:-1] + ' ' + 'A' * 64 + ' x' + 'b' * 64 + 'x').encode()
    text_names = ['stderr.txt', 'stdout.txt', 'trace.jsonl', 'traceback.txt', 'guardian-reaps.jsonl',
                  'completed', 'other-text']
    binary_names = ['rtf', 'docx', 'pdf', 'unknown', 'nul', 'archive.txt']
    payloads = {'detached-helper.' + name: text for name in text_names}
    payloads.update({'detached-helper.' + name: text for name in ('rtf', 'docx', 'pdf')})
    payloads.update({'detached-helper.unknown': b'\xff' + text,
                     'detached-helper.nul': b'\x00' + text,
                     'detached-helper.archive.txt': b'PK\x03\x04' + text})
    payloads['detached-helper.unreadable.txt'] = None
    class FakeDocker:
        def run(self, command, **kwargs):
            assert command[0] != 'cp', 'raw host copy is forbidden'
            return subprocess.CompletedProcess([], 0, '', '')
        def exec(self, name, command, **kwargs):
            code = command[2]
            if 'entries = []' in code:
                entries = [{'path': '/fixture/' + name, 'name': name, 'type': 'file'} for name in payloads]
                return subprocess.CompletedProcess([], 0, json.dumps(entries), '')
            if 'base64.b64encode(payload)' in code:
                payload = payloads[Path(command[3]).name]
                # Run the actual embedded transport script, no container and no real reads.
                with monkeypatch.context() as context:
                    context.setattr(Path, 'read_bytes', Mock(side_effect=PermissionError()) if payload is None else Mock(return_value=payload))
                    context.setattr(sys, 'argv', ['-c', command[3]])
                    context.setattr(sys, 'stdout', io.StringIO())
                    exec(compile(code, 'artifact transport', 'exec'), {})
                    output = sys.stdout.getvalue()
                return subprocess.CompletedProcess([], 0, output, '')
            if 'load_config' in code:
                return subprocess.CompletedProcess([], 0, '{"records": [], "config_error": null}', '')
            return subprocess.CompletedProcess([], 0, '{}', '')
    _, functions = scenario_diagnostics(tmp_path, monkeypatch, FakeDocker())
    writes = []
    write_bytes = Path.write_bytes
    def first_write(path, payload):
        if path.parent.name in ('helper', 'service'):
            writes.append((path.name, payload))
            if path.name.removeprefix('detached-helper.') in text_names:
                assert payload == ('[redacted-capability] [redacted-capability] [redacted-capability] x' + 'b' * 64 + 'x').encode()
            else:
                assert payload == payloads[path.name]
        return write_bytes(path, payload)
    monkeypatch.setattr(Path, 'write_bytes', first_write)
    functions['detached_export']('test')
    assert len(writes) == 2 * (len(text_names) + len(binary_names))
    manifest = json.loads((tmp_path / 'detached-debug/test/artifacts.json').read_text())
    assert sum(record['retained'] for record in manifest) == len(writes)
    assert all(record['error_type'] == 'PermissionError' for record in manifest if not record['retained'])
    assert 'artifact unreadable: PermissionError' in (tmp_path / 'detached-debug/capture-errors.jsonl').read_text()
    assert not (tmp_path / 'detached-debug/test/artifacts/helper/detached-helper.unreadable.txt').exists()
    assert (tmp_path / 'detached-debug/traceback.txt').read_bytes() == writes[0][1]
