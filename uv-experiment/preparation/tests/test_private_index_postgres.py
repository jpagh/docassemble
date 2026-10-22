# ruff: noqa: F811 -- explicit pytest fixture registration.
"""Real retained private-source attestation through both PostgreSQL schema paths."""
from docassemble.base.managed_python import python_target
import copy
import json

import pytest
from sqlalchemy import update

from test_preparation_postgres import SOURCE, protocol  # noqa: F401
from docassemble_executor.preparation import PreparationError
from docassemble_executor.registry_builder import verify_retained


def test_private_index_retained_readiness_requires_redacted_binding_preview(protocol):
    evidence = SOURCE / 'uv-experiment/05a2b4/evidence/accepted/source'
    ready = json.loads((evidence / 'attestation.json').read_text())
    retained = evidence / 'retained' / ready['bundle']['digest']
    verify_retained(retained, ready['bundle']['digest'])
    sources = json.loads((retained / 'private-sources.json').read_text())
    core = {'private-core': ready['bundle']['inventory']['private-core']}
    baseline = {'core': core, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                'config': {'private_indexes': {'alpha': sources[0]['url']},
                           'private_bindings': {name: 'alpha' for name in ready['bundle']['inventory']}},
                'desired_inventory': core, 'runtime': {'kind': 'normal', 'inventory': core}}
    with protocol.engine.begin() as connection:
        connection.execute(update(protocol.tables['environment_state']).values(snapshot=baseline))
        connection.execute(protocol.tables['requirement_contribution'].delete())
    candidate = protocol.prepare('alice', 'private-one[feature]>=1; python_version >= "3.12"', 'private-builder')
    claim = protocol.claim(candidate)
    missing = copy.deepcopy(ready)
    missing['warnings'] = []
    with pytest.raises(PreparationError, match='invalid ready result'):
        protocol.publish_ready(claim, missing)
    assert protocol.publish_ready(claim, ready)
    status = protocol.status('alice', candidate)
    assert status['preview'] == ready
    assert status['installation_requested'] is False
    assert 'DISPOSABLE_PRIVATE_TOKEN' not in json.dumps(status, default=str)
    with protocol.engine.connect() as connection:
        environment = protocol._environment(connection)
        assert environment['snapshot'] == baseline
