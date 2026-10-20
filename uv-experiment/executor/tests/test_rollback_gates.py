"""Rollback publication uses normal immutable gates, including prior-core retention."""
import copy
from dataclasses import replace

import pytest

from test_selected_baseline import upgrade, retained_bundle  # noqa: F401 -- pytest fixture
from docassemble_executor.installation import InstallationFailure
from docassemble_executor.selected_baseline import load_baselines


def image_rollback(upgrade, monkeypatch):  # noqa: F811 -- pytest fixture
    poller, upgrade_claim, image = upgrade
    poller._gates(upgrade_claim)
    historical = dict(image, publication={'result': {'bundle': retained_bundle(poller.store, image['core'])}})
    snapshot = dict(image, selected_baseline=None, schema_mode='unchanged',
                    recovery_baseline=upgrade_claim.snapshot['selected_baseline'],
                    publication={'inputs': {'baseline': copy.deepcopy(upgrade_claim.snapshot),
                        'change_operation': 'rollback', 'rollback': {'snapshot': historical},
                        'ownership_effects': {'identities': {}}},
                        'result': historical['publication']['result']})
    monkeypatch.setattr('docassemble_executor.rollback_schema.retained_schema_policy',
                        lambda _: {'known_incompatible_revisions': ['bad']})
    return poller, replace(upgrade_claim, snapshot=snapshot)


@pytest.mark.parametrize('observed', ['advanced-unproven', 'bad', None])
def test_rollback_gates_distinguish_schema_and_preserve_desired_material(upgrade, monkeypatch, observed):  # noqa: F811
    poller, claim = image_rollback(upgrade, monkeypatch)
    monkeypatch.setattr('docassemble_executor.alembic_probe.read_database_revision', lambda *_: observed)
    if observed == 'advanced-unproven':
        assert poller._gates(claim).is_dir()
        local = load_baselines(poller.paths)
        assert local['schema_mode'] == 'unchanged' and local['selected_baseline'] is None
        assert local['recovery_baseline'] == claim.snapshot['recovery_baseline']
        assert poller._gates(claim, mutation=False).is_dir()
    else:
        with pytest.raises(InstallationFailure, match='schema-incompatible' if observed else 'schema-unreadable'):
            poller._gates(claim)


def test_missing_prior_core_blocks_image_rollback_before_mutation(upgrade, monkeypatch):  # noqa: F811
    poller, claim = image_rollback(upgrade, monkeypatch)
    prior = poller.store / claim.snapshot['recovery_baseline']['core_bundle']['digest'] / 'uv.lock'
    prior.unlink()
    with pytest.raises(InstallationFailure, match='bundle-invalid'):
        poller._gates(claim)


@pytest.mark.parametrize('mode', ['upgrade', 'unchanged'])
@pytest.mark.parametrize('observed', ['advanced-unproven', 'bad', None])
def test_replacement_checks_new_image_recovery_and_known_incompatible_database_before_mutation(upgrade, monkeypatch, mode, observed):  # noqa: F811
    poller, claim, image = upgrade
    bundle = retained_bundle(poller.store, image['core'])
    snapshot = dict(image, selected_baseline=None, schema_mode=mode,
        migration={'state': 'published'}, schema_release={'release': '1.0', 'source': 'selected-baseline-fallback', 'image_baseline': image['image_baseline']}, recovery_baseline={'image_baseline': image['image_baseline'], 'core': image['core']},
        publication={'inputs': {'baseline': copy.deepcopy(image), 'change_operation': 'migration', 'ownership_effects': {}},
                     'result': {'bundle': bundle}})
    monkeypatch.setattr('docassemble_executor.image_baseline.verify_image_recovery_core', lambda *_: None)
    monkeypatch.setattr('docassemble_executor.rollback_schema.retained_schema_policy',
                        lambda _: {'known_incompatible_revisions': ['bad']})
    monkeypatch.setattr('docassemble_executor.alembic_probe.read_database_revision', lambda *_: observed)
    claim = replace(claim, snapshot=snapshot)
    if observed == 'advanced-unproven':
        assert poller._gates(claim).is_dir()
        local = load_baselines(poller.paths)
        assert local['recovery_baseline'] == snapshot['recovery_baseline']
        assert local['selected_baseline'] is None
    else:
        with pytest.raises(InstallationFailure, match='schema-incompatible' if observed else 'schema-unreadable'):
            poller._gates(claim)
