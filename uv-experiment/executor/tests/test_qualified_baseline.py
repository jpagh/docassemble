"""Image-qualified source-policy gate: operator private baseline and effective comparison."""
from docassemble.base.managed_python import python_target
import json

import pytest

from docassemble.base.managed_config import dependency_snapshot
from docassemble.base.managed_preparation import PreparationError
from docassemble.base.managed_qualification import configured_baseline, require_qualified_inputs

PUBLIC = {'index_url': 'https://pypi.org/simple'}
PRIVATE = {'private_indexes': {'alpha': 'https://packages.invalid/private/simple/'},
           'private_bindings': {'example': 'alpha'}}


def baseline_document(config):
    return {'format': 1, 'baseline': {
        'target': python_target(), 'tool': {'uv': '0.12.21'}, 'config': config,
        'core': {'core': {'version': '1.0', 'source_digest': 'a' * 64}},
        'image_baseline': {'image_target': 'fixture', 'bundle': 'runtime-r1', 'bundle_digest': 'b' * 64}}}


@pytest.mark.parametrize('config', [PUBLIC, PRIVATE])
def test_configured_baseline_accepts_public_and_operator_private_policy(tmp_path, monkeypatch, config):
    descriptor = tmp_path / 'baseline.json'
    descriptor.write_text(json.dumps(baseline_document(config)))
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(descriptor))
    monkeypatch.delenv('CONTAINERROLE', raising=False)
    assert configured_baseline()['config'] == config


@pytest.mark.parametrize('config', [
    {'index_url': 'https://user:secret@packages.invalid/simple/'},
    {'private_indexes': {'alpha': 'https://user:secret@packages.invalid/simple/'}, 'private_bindings': {'example': 'alpha'}},
    {'private_indexes': {'alpha': 'https://packages.invalid/private/simple/'}, 'private_bindings': {'example': 'missing'}},
    {'private_indexes': {'alpha': 'http://packages.invalid/simple/'}, 'private_bindings': {'example': 'alpha'}},
    {'private_indexes': {'alpha': 'https://packages.invalid/private/simple/'}, 'private_bindings': {'example': 'alpha'},
     'private_tls_exceptions': ['alpha', 'alpha']},
    {'private_indexes': {}},
    {'unsupported': True},
])
def test_configured_baseline_rejects_invalid_or_secret_source_policy(tmp_path, monkeypatch, config):
    descriptor = tmp_path / 'baseline.json'
    descriptor.write_text(json.dumps(baseline_document(config)))
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(descriptor))
    monkeypatch.delenv('CONTAINERROLE', raising=False)
    with pytest.raises(PreparationError, match='invalid qualified image baseline') as error:
        configured_baseline()
    assert 'secret' not in str(error.value)


def qualified_inputs(config, *, baseline_config, requirement='example>=1', change_operation=None):
    inputs = {'baseline': {'core': {}, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                           'config': baseline_config, 'image_baseline': {'image_target': 'fixture'}},
              'ownership_effects': {}, 'requirement': {'normalized_name': requirement.split('>')[0]}}
    baseline = {'core': {}, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                'config': config, 'image_baseline': {'image_target': 'fixture'}}
    if change_operation:
        inputs['change_operation'] = change_operation
        inputs['config_import'] = {'generation': 1, 'snapshot': dependency_snapshot(
            {'python packages': ['example>=1'], 'private indexes': {'alpha': 'https://packages.invalid/private/simple/'},
             'private bindings': {'example': 'alpha'}}, environ={})}
    return inputs, baseline


def test_require_qualified_inputs_accepts_matching_operator_private_baseline():
    inputs, baseline = qualified_inputs(PRIVATE, baseline_config=PRIVATE)
    require_qualified_inputs(inputs, baseline)


def test_require_qualified_inputs_rejects_committed_private_policy_on_public_baseline():
    inputs, baseline = qualified_inputs(PUBLIC, baseline_config=PRIVATE)
    with pytest.raises(PreparationError, match='unsupported image baseline'):
        require_qualified_inputs(inputs, baseline)


def test_require_qualified_inputs_rejects_private_import_on_public_baseline():
    inputs, baseline = qualified_inputs(PUBLIC, baseline_config=PUBLIC, change_operation='config')
    with pytest.raises(PreparationError, match='unsupported image baseline'):
        require_qualified_inputs(inputs, baseline)


def test_require_qualified_inputs_accepts_effective_private_import_on_private_baseline():
    inputs, baseline = qualified_inputs(PRIVATE, baseline_config=PUBLIC, change_operation='config')
    require_qualified_inputs(inputs, baseline)


def test_require_qualified_inputs_accepts_public_config_import_on_public_baseline():
    inputs, baseline = qualified_inputs(PUBLIC, baseline_config=PUBLIC, change_operation='config')
    inputs['config_import']['snapshot'] = dependency_snapshot({'python packages': ['example>=1']}, environ={})
    require_qualified_inputs(inputs, baseline)


@pytest.mark.parametrize('target', [None, {}, [], {'python': ['3.14']}, {'python': '3.12'},
    {'python': '3.13'}, {'python': '>=3.12'}, {'python': '3.14.4'}, {'python': '3.14', 'extra': 'key'}])
def test_configured_baseline_exact_observed_target_refusal(tmp_path, monkeypatch, target):
    import sys
    monkeypatch.setattr(sys, 'version_info', (3, 14, 4))
    descriptor = tmp_path / 'baseline.json'
    document = baseline_document(PUBLIC)
    document['baseline']['target'] = target
    descriptor.write_text(json.dumps(document))
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(descriptor))
    monkeypatch.delenv('CONTAINERROLE', raising=False)
    with pytest.raises(PreparationError, match='invalid qualified image baseline'):
        configured_baseline()
