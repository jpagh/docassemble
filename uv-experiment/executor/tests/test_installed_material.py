"""Immutable image material is authoritative, not rewritable package RECORDs."""
from docassemble.base.managed_python import python_site_packages

import hashlib

from docassemble_executor.inventory import verify_environment
from test_inventory import fixture_environment


def test_installed_material_must_match_retained_image_even_when_record_is_rewritten(tmp_path):
    venv = fixture_environment(tmp_path)
    site = venv / python_site_packages()
    (site / 'fixture.py').write_text('answer = 0\n')
    (site / 'fixture-1.0.dist-info/RECORD').write_text('fixture.py,,\n')
    expected = {(python_site_packages() / 'fixture.py').as_posix(): hashlib.sha256(b'answer = 42\n').hexdigest()}
    assert not verify_environment(venv, {'fixture': '1.0'}, expected).ok
