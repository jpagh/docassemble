"""Generated or bundled bytecode is not portable installed-material evidence."""
from docassemble.base.managed_python import python_site_packages

from docassemble_executor.inventory import inspect_material
from test_inventory import fixture_environment


def test_material_excludes_bytecode_even_when_a_wheel_records_its_hash(tmp_path):
    venv = fixture_environment(tmp_path)
    site = venv / python_site_packages()
    (site / 'fixture.pyc').write_bytes(b'nonportable bytecode')
    with (site / 'fixture-1.0.dist-info/RECORD').open('a') as record:
        record.write('fixture.pyc,sha256=ignored,19\n')
    assert all(not name.endswith('.pyc') for name in inspect_material(venv))
