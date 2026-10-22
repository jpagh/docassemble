"""Installed-target validation through the filesystem inventory interface."""
from docassemble.base.managed_python import python_site_packages

import base64
import hashlib

from docassemble_executor.inventory import inspect_environment, verify_environment


def fixture_environment(tmp_path):
    site = tmp_path / python_site_packages()
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
    (venv / python_site_packages() / 'fixture.py').unlink()
    assert not verify_environment(venv, {'fixture': '1.0'}).ok


def test_matching_metadata_does_not_hide_modified_package(tmp_path):
    venv = fixture_environment(tmp_path)
    (venv / python_site_packages() / 'fixture.py').write_text('answer = 0\n')
    assert not verify_environment(venv, {'fixture': '1.0'}).ok


def test_inventory_rejects_unrecorded_installed_distributions(tmp_path):
    venv = fixture_environment(tmp_path)
    assert not verify_environment(venv, {}).ok


def test_wheel_payload_material_uses_observed_prefix_for_purelib_platlib_and_overlaps(tmp_path, monkeypatch):
    import sys
    from zipfile import ZipFile
    from docassemble_executor.inventory import wheel_payload_material
    monkeypatch.setattr(sys, 'version_info', (3, 14, 4))
    for number in (1, 2):
        with ZipFile(tmp_path / f'example{number}-1-py3-none-any.whl', 'w') as wheel:
            wheel.writestr('namespace/shared.py', str(number))
            wheel.writestr('example.data/purelib/pure.py', 'pure')
            wheel.writestr('example.data/platlib/native.so', 'native')
    material = wheel_payload_material(tmp_path)
    prefix = 'lib/python3.14/site-packages/'
    assert set(material) == {prefix + name for name in ('namespace/shared.py', 'pure.py', 'native.so')}
    assert len(material[prefix + 'namespace/shared.py']) == 2
