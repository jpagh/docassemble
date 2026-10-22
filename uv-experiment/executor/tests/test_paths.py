"""Managed runtime defaults remain distinct from the stock image venv."""
import sys
from pathlib import Path

from docassemble_executor.paths import ExecutorPaths


def test_executor_paths_derives_distinct_runtime_and_preserves_explicit_override(monkeypatch):
    monkeypatch.setattr(sys, 'version_info', (3, 14, 4))
    monkeypatch.delenv('DA_ROOT', raising=False)
    monkeypatch.delenv('DA_RUNTIME_VENV', raising=False)
    assert ExecutorPaths.from_env().runtime_venv == Path('/usr/share/docassemble/managed-runtime3.14')
    assert ExecutorPaths.from_env().runtime_venv != Path('/usr/share/docassemble/local3.14')
    monkeypatch.setenv('DA_ROOT', '/test')
    monkeypatch.setattr(sys, 'version_info', (3, 13, 9))
    assert ExecutorPaths.from_env().runtime_venv == Path('/test/managed-runtime3.13')
    monkeypatch.setenv('DA_RUNTIME_VENV', '/explicit')
    assert ExecutorPaths.from_env().runtime_venv == Path('/explicit')
