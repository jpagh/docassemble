"""Installed-target validation through the filesystem inventory interface."""

import base64
import hashlib

from docassemble_executor.inventory import inspect_environment, verify_environment


def fixture_environment(tmp_path):
    site = tmp_path / 'lib/python3.12/site-packages'
    site.mkdir(parents=True)
    (site / 'fixture.py').write_text('answer = 42\n')
    info = site / 'fixture-1.0.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text('Name: fixture\nVersion: 1.0\n')
    digest = base64.urlsafe_b64encode(hashlib.sha256(b'answer = 42\n').digest()).decode().rstrip('=')
    (info / 'RECORD').write_text('fixture.py,sha256=' + digest + ',12\n')
    return tmp_path


def test_inventory_records_exact_installed_distributions(tmp_path):
    venv = fixture_environment(tmp_path)
    assert inspect_environment(venv) == {'fixture': '1.0'}
    assert verify_environment(venv, {'fixture': '1.0'}).ok


def test_matching_metadata_does_not_hide_deleted_package(tmp_path):
    venv = fixture_environment(tmp_path)
    (venv / 'lib/python3.12/site-packages/fixture.py').unlink()
    assert not verify_environment(venv, {'fixture': '1.0'}).ok


def test_matching_metadata_does_not_hide_modified_package(tmp_path):
    venv = fixture_environment(tmp_path)
    (venv / 'lib/python3.12/site-packages/fixture.py').write_text('answer = 0\n')
    assert not verify_environment(venv, {'fixture': '1.0'}).ok


def test_inventory_rejects_unrecorded_installed_distributions(tmp_path):
    venv = fixture_environment(tmp_path)
    assert not verify_environment(venv, {}).ok
