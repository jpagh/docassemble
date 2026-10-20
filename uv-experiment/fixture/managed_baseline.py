"""Qualification scaffolding ONLY; copied into disposable fixture wheels.

Production never imports/ships this module. This is explicit seeded test intent,
not startup adoption, registry preparation authority or inferred installed state.
"""
import json
from pathlib import Path

from sqlalchemy import select
from docassemble.base.managed_records import preparation_tables


def seed(engine, prefix, manifest_path):
    manifest = json.loads(Path(manifest_path).read_text())
    tables = preparation_tables(prefix)
    baseline = {'fixture_only': True, 'core': manifest['inventories']['core-r1'],
                'target': manifest['identity'], 'tool': {'uv_sha256': manifest['uv_binary_sha256']},
                'config': {}, 'desired_inventory': manifest['inventories']['runtime-r1']['distributions'],
                'runtime': {'kind': 'unknown', 'inventory': {}}}
    with engine.begin() as connection:
        if connection.execute(select(tables['environment_state'].c.id)).first():
            return
        connection.execute(tables['revision'].insert().values(id='lifecycle-fixture-r1', snapshot=baseline))
        connection.execute(tables['environment_state'].insert().values(id=1, managed=True,
            desired_revision_id='lifecycle-fixture-r1', publication_generation=1,
            input_generation=1, target_generation=1, runtime_generation=1, snapshot=baseline))
