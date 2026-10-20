"""Image-build ONLY: wrap fixture create_tables, never repository production code."""
from pathlib import Path
import shutil
import sys


WRAPPER = '''

# Disposable lifecycle fixture ONLY. Not a production initialization authority.
_fixture_populate_tables = populate_tables

def populate_tables(*args, **kwargs):
    from docassemble.webapp.fixture_managed_baseline import seed
    with get_flask_app().app_context():
        seed(db.engine, dbtableprefix, '/usr/share/docassemble/managed/bundles/manifest.json')
    return _fixture_populate_tables(*args, **kwargs)
'''


def main():
    bundle_root = Path(sys.argv[1])
    for name in ('core-r1', 'runtime-r1', 'runtime-r2', 'runtime-fault'):
        webapp = bundle_root / name / 'sources/docassemble_webapp/docassemble/webapp'
        shutil.copyfile(Path(__file__).with_name('managed_baseline.py'), webapp / 'fixture_managed_baseline.py')
        path = webapp / 'create_tables.py'
        path.write_text(path.read_text() + WRAPPER)


if __name__ == '__main__':
    main()
