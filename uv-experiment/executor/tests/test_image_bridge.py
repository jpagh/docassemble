"""Experimental image bridge ingress and local terminal boundaries."""
import ast
from pathlib import Path
import urllib.error
import urllib.request

import pytest

from docassemble.base.managed_qualification import bridge_http_path, configured_baseline
from docassemble_executor.admission import Admission, start_ingress
from docassemble_executor.journal import Journal, Operation
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.startup import validate_startup, StartupError


def test_bridge_ingress_enumerates_and_rejects_every_non_allowlisted_route(tmp_path, monkeypatch):
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', '/operator/explicit.json')
    ingress = start_ingress(0, 1, Admission(tmp_path), lambda: {})
    root = Path(__file__).resolve().parents[3] / 'docassemble_webapp/docassemble/webapp'
    paths = {'/unqualified', '/socket.io/other'}
    for source in root.rglob('*.py'):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and
                    node.func.attr == 'route' and node.args and isinstance(node.args[0], ast.Constant) and
                    isinstance(node.args[0].value, str)):
                route = node.args[0].value
                if not bridge_http_path(route):
                    paths.add(route.replace('<', '').replace('>', '').replace(' ', ''))
    try:
        for path in sorted(paths):
            for method in ('GET', 'POST'):
                request = urllib.request.Request(f'http://127.0.0.1:{ingress.server_port}{path}', method=method)
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                assert error.value.code == 403, path
        assert len(paths) > 100
    finally:
        ingress.shutdown()
        ingress.server_close()


def test_bridge_opt_in_is_not_a_default(monkeypatch):
    monkeypatch.delenv('DA_QUALIFIED_IMAGE_BASELINE', raising=False)
    with pytest.raises(Exception, match='qualified all-role'):
        configured_baseline()
    for path in ('/health_check', '/health_status', '/status.json', '/user/sign-in', '/user/sign-out',
                 '/static/app.css', '/package_preview', '/api/package_preview',
                 '/api/package', '/api/package_update_status', '/updatepackage', '/createplaygroundpackage'):
        assert bridge_http_path(path)


def test_configured_bridge_boot_before_journal_is_also_fenced(tmp_path, monkeypatch):
    from types import SimpleNamespace
    descriptor = tmp_path / 'baseline.json'
    descriptor.write_text('{}')
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(descriptor))
    with pytest.raises(StartupError, match='requires independent reconciliation'):
        validate_startup(SimpleNamespace(paths=ExecutorPaths.from_env(), journal=Journal()))


def test_published_boot_is_explicitly_fenced(tmp_path):
    from types import SimpleNamespace
    journal = Journal(operation=Operation('attempt', 'published-apply', 'revision'))
    with pytest.raises(StartupError, match='requires independent reconciliation'):
        validate_startup(SimpleNamespace(paths=ExecutorPaths.from_env(), journal=journal))


def test_normal_reporting_replay_rechecks_health_without_installer_lock(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from docassemble_executor.authority import RuntimeLock
    from docassemble_executor.installation import InstallationPoller
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path)
    receipt = {'identity': {'attempt_id': 'a', 'bundle_digest': 'digest'}, 'outcome': 'applied-normal'}
    directory = tmp_path / 'digest'
    directory.mkdir()
    (directory / 'installed-material.json').write_text('{}')
    monkeypatch.setattr('docassemble_executor.installation.verify_retained', lambda *a: None)
    calls = []
    poller = InstallationPoller(SimpleNamespace(report_terminal=lambda value: calls.append('report') or 'replayed',
        invalidate_normal=lambda value: calls.append('invalidate')), paths, tmp_path, tmp_path)
    monkeypatch.setattr('docassemble_executor.published.recheck_published', lambda *a: True)
    monkeypatch.setattr('docassemble_executor.published.open_published', lambda *a: calls.append('open'))
    with RuntimeLock(tmp_path, shared=True):
        assert poller._report_normal(receipt) == 'replayed'
    assert calls == ['report', 'open']
    monkeypatch.setattr('docassemble_executor.published.recheck_published', lambda *a: False)
    monkeypatch.setattr('docassemble_executor.published.close_published', lambda *a: calls.append('close'))
    with pytest.raises(Exception, match='health failed'):
        poller._report_normal(receipt)
    assert calls == ['report', 'open', 'close', 'invalidate']
