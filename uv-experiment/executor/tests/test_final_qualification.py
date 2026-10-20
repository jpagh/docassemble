"""The final qualification runner must not turn a subset or failure into green."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def qualification(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'run-qualification.py'
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location('final_qualification', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_final_qualification_requires_every_critical_phase(qualification):
    rows = [{'phase': phase, 'exit': 0} for phase in qualification.PHASES]
    assert qualification.complete(rows)
    assert not qualification.complete(rows[:-1])
    assert not qualification.complete([])
    rows[-1]['exit'] = 1
    assert not qualification.complete(rows)


def test_final_qualification_requires_local_and_both_cloud_restores(qualification):
    assert {'restore-local', 'restore-s3', 'restore-azure'} <= set(qualification.PHASES)
    assert {'browser-api', 'registry', 'config_publication', 'playground_install',
            'direct_url', 'private_git', 'private_index_credentials'} <= set(qualification.PHASES)
    assert len(qualification.PHASES) == len(set(qualification.PHASES))


def test_final_qualification_hashes_its_resource_safety_helper(qualification):
    assert 'uv-experiment/proof_resources.py' in qualification.source_hashes()


def test_final_qualification_counts_only_audited_uv_verbs(qualification):
    assert qualification.uv_counts('999\tlock --offline\n999\tsync --frozen\n999\tsync --frozen\n'
                                   'a diagnostic mentioning build and lock\n0\t--version\n999\tbuild .\n') == {
        'lock': 1, 'sync': 2, 'build': 1, 'pip': 0}


def test_final_qualification_protects_host_services_and_shared_test_infrastructure(qualification):
    assert set(qualification.PROTECTED) == {'caddy', 'vaultwarden', 'ja', 'int', 'backup', 'da-postgres', 'da-redis'}
