"""All retained wheel payloads are valid for a multiply-owned namespace file."""
from docassemble.base.managed_python import python_site_packages

from docassemble_executor.inventory import inspect_material, verify_environment
from test_inventory import fixture_environment


def test_reinstall_accepts_either_image_recorded_namespace_payload(tmp_path):
    venv = fixture_environment(tmp_path)
    site = venv / python_site_packages()
    other = site / 'other-1.0.dist-info'
    other.mkdir()
    (other / 'METADATA').write_text('Name: other\nVersion: 1.0\n')
    # Known SHA-256 of empty bytes, a second legitimate namespace payload.
    (other / 'RECORD').write_text('fixture.py,sha256=47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU,0\n')
    material = inspect_material(venv)
    (site / 'fixture.py').write_bytes(b'')
    assert verify_environment(venv, {'fixture': '1.0', 'other': '1.0'}, material).ok
    (site / 'fixture.py').write_text('unrecorded payload')
    assert not verify_environment(venv, {'fixture': '1.0', 'other': '1.0'}, material).ok
