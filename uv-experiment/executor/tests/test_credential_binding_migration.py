"""Frozen original candidate schema and a single additive migration head."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[3]
VERSIONS = ROOT / 'docassemble_webapp/docassemble/webapp/alembic/versions'


def test_original_migration_column_set_stays_frozen_and_head_is_single(monkeypatch):
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.database', SimpleNamespace(dbtableprefix='test_', dbprefix='postgresql', daconfig={}))
    spec = importlib.util.spec_from_file_location('frozen_preparation_migration', VERSIONS / '05a100000001_managed_preparation.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    created = []
    monkeypatch.setattr(migration, 'op', SimpleNamespace(execute=lambda ddl: created.append(ddl),
                                                       get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='sqlite'))))
    migration.upgrade()
    candidate = next(ddl.element for ddl in created if ddl.element.name == 'test_managed_candidate')
    assert list(candidate.c.keys()) == ['id', 'requester_id', 'operation', 'request_digest', 'parent_id',
        'publication_generation', 'input_generation', 'target_generation', 'runtime_generation',
        'inputs', 'state', 'fence', 'claim_until', 'result', 'result_digest', 'ready_at', 'expires_at']
    config = Config()
    config.set_main_option('script_location', str(VERSIONS.parent))
    assert ScriptDirectory.from_config(config).get_heads() == ['05b500000001']


def test_fixture_migration_follows_the_production_head_without_branching():
    """The proof-only fixture migration must extend the production head.

    It ships in the qualified image alongside the production versions, so
    branching from the same parent would create two heads and fresh database
    initialization would refuse - the failure mode that broke the container
    qualification at first boot (the web service never became ready).
    """
    import ast

    def revision_of(path):
        values = {}
        for node in ast.parse(path.read_text()).body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and
                    isinstance(node.targets[0], ast.Name) and node.targets[0].id in ('revision', 'down_revision')):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        return values['revision'], values['down_revision']

    fixture = ROOT / 'uv-experiment/fixture/fixture_advanced_revision.py'
    graph = [revision_of(path) for path in [*VERSIONS.glob('*.py'), fixture]]
    revisions = {revision for revision, _ in graph}
    parents = {parent for _, parent in graph if parent}
    # Exactly one head across production plus the shipped fixture migration, and
    # the fixture migration is what terminates the chain.
    assert sorted(revisions - parents) == ['fixture_advanced_revision']
    assert fixture.read_text().count("down_revision = '05b500000001'") == 1
