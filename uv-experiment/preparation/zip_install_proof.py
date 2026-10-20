"""Pinned uv 0.12.21 real retained-wheel/sdist solve and cold-cache build proof."""
from pathlib import Path
import json
import shutil
import tempfile

from docassemble.base.managed_artifacts import distribution_identity, file_digest, retain_artifact, open_regular
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained
from docassemble.base.managed_preparation import PreparationError
from zip_fixture import distribution


def prove(root, wheel):
    root.mkdir(mode=0o700)
    store, bundles = root / 'artifacts', root / 'bundles'
    store.mkdir(mode=0o700)
    upload = distribution(root / 'misleading.zip', wheel=wheel, extras={'feature': ['idna==3.11']})
    with open_regular(upload) as source:
        identity = dict(distribution_identity(source), digest=file_digest(source))
        retain_artifact(store, identity['digest'], source)
    upload.unlink()
    inputs = {'artifact': identity, 'requirement': {'declaration': 'zip-example[feature]==1.0'},
              'contributions': [{'id': 'config', 'origin': 'config', 'declaration': 'zip-example<2'}],
              'ownership_effects': {'owner_id': 'fixture'},
              'baseline': {'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
                           'config': {'index_url': 'https://pypi.org/simple'}, 'core': {},
                           'desired_inventory': {}, 'runtime': {'kind': 'normal', 'inventory': {}}}}
    builder = RegistryBuilder(bundles, shutil.which('uv'), artifact_root=store)
    result = builder.build(inputs)
    verify_retained(bundles / result['bundle']['digest'], result['bundle']['digest'])
    expected = {'version': '1.0', 'source_digest': identity['digest']}
    assert result['bundle']['inventory']['zip-example'] == expected
    core = result['bundle']['inventory']['idna']
    assert core['version'] == '3.11'
    assert result['desired_diff'] == result['runtime_diff'] == {
        name: {'before': None, 'after': value} for name, value in result['bundle']['inventory'].items()}
    assert (bundles / result['bundle']['digest'] / 'artifacts' / identity['filename']).read_bytes() == (store / identity['digest']).read_bytes()
    # An unrelated solve must still use the committed local artifact, not PyPI,
    # the deleted legacy upload, or the discarded per-build cache.
    del inputs['artifact']
    inputs['contributions'] = [{'id': 'committed', 'declaration': 'zip-example[feature]==1.0', 'artifact_ref': identity['digest']},
                               {'id': 'config', 'origin': 'config', 'declaration': 'zip-example<2'}]
    inputs['baseline']['core'] = {'idna': core}
    inputs['baseline']['desired_inventory'] = result['bundle']['inventory']
    inputs['baseline']['runtime']['inventory'] = result['bundle']['inventory']
    inputs['requirement'] = {'declaration': 'zip-example>=1'}
    again = builder.build(inputs)
    assert again['bundle']['inventory'] == result['bundle']['inventory']
    assert again['desired_diff'] == again['runtime_diff'] == {}
    inputs['contributions'][1]['declaration'] = 'zip-example<1'
    try:
        builder.build(inputs)
    except PreparationError as error:
        assert str(error) == 'retained artifact metadata does not satisfy declarations'
    else:
        raise AssertionError('retained constraint was relaxed or substituted')
    inputs['requirement'] = {'declaration': 'zip-example; sys_platform == "never-a-real-platform"'}
    inputs['artifact'] = identity
    inputs['contributions'] = []
    inactive = builder.build(inputs)
    assert inactive['bundle']['inventory'] == {'idna': core}
    assert inactive['desired_diff'] == inactive['runtime_diff'] == {'zip-example': {'before': expected, 'after': None}}
    assert identity['digest'] in inactive['bundle']['artifacts']
    return {'format': 'wheel' if wheel else 'sdist', 'artifact': identity['digest'], 'bundle': result['bundle']['digest'],
            'inventory': result['bundle']['inventory'], 'retained_reuse': True, 'cold_cache_verified': True,
            'exact_core_verified': True, 'inactive_marker_preserved': True, 'extra_dependency_selected': True,
            'retained_constraint_conflict_rejected': True}


def prove_transitive(root):
    root.mkdir(mode=0o700)
    store = root / 'artifacts'
    store.mkdir(mode=0o700)
    contributions = []
    for name, requires in [('zip_example', []), ('dependent', ['zip-example<1'])]:
        upload = distribution(root / (name + '.zip'), name=name, requires=requires)
        with open_regular(upload) as source:
            identity = dict(distribution_identity(source), digest=file_digest(source))
            retain_artifact(store, identity['digest'], source)
        upload.unlink()
        contributions.append({'id': name, 'declaration': identity['normalized_name'] + '==1.0',
                              'artifact_ref': identity['digest']})
    inputs = {'requirement': {'declaration': 'zip-example==1.0'}, 'contributions': contributions,
              'ownership_effects': {}, 'baseline': {'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
              'config': {'index_url': 'https://pypi.org/simple'}, 'core': {}, 'desired_inventory': {},
              'runtime': {'kind': 'normal', 'inventory': {}}}}
    try:
        RegistryBuilder(root / 'bundles', shutil.which('uv'), artifact_root=store).build(inputs)
    except PreparationError as error:
        assert str(error) == 'registry preparation command failed'
    else:
        raise AssertionError('uv ignored a transitive path-source version constraint')
    return {'transitive_path_constraint_rejected_by_uv': True}


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='zip-install-proof-') as directory:
        print(json.dumps({'uv': '0.12.21', 'proofs': [prove(Path(directory) / kind, wheel)
              for kind, wheel in [('wheel', True), ('sdist', False)]],
              'transitive_probe': prove_transitive(Path(directory) / 'transitive')}, sort_keys=True))
        print('PASS: retained ZIP solve/build, exact source, complete diffs and cold-cache reuse')
