"""Real pinned-uv, disposable-container proof; no runtime or DB mutation."""
import copy
import json
from pathlib import Path
import urllib.request

from docassemble_executor.preparation import PreparationError
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained


def assert_conflict_rejected(builder, inputs):
    conflict = copy.deepcopy(inputs)
    conflict['contributions'] = [{'id': 'conflicting-six', 'declaration': 'six==1.16.0'}]
    original_conflict = copy.deepcopy(conflict)
    try:
        builder.build(conflict)
    except PreparationError as error:
        assert str(error) == 'registry preparation command failed'
    else:
        raise AssertionError('conflict accepted')
    assert conflict == original_conflict


def main():
    with urllib.request.urlopen('https://pypi.org/pypi/packaging/25.0/json') as response:
        metadata = json.load(response)
    source_hash = next(v['digests']['sha256'] for v in metadata['urls'] if v['packagetype'] == 'sdist')
    core = {'packaging': {'version': '25.0', 'source_digest': source_hash}}
    inputs = {'baseline': {'core': core, 'target': {'python': '3.12'},
              'tool': {'uv': '0.12.21'}, 'config': {'index_url': 'https://pypi.org/simple'},
              'desired_inventory': core, 'runtime': {'kind': 'normal', 'inventory': core}},
              'contributions': [], 'requirement': {'declaration': 'six==1.17.0'},
              'ownership_effects': {'normalized_name': 'six', 'owner_id': 'alice', 'existing': False}}
    original = copy.deepcopy(inputs)
    builder = RegistryBuilder(Path('/tmp/proof/retained'))
    result = builder.build(inputs)
    assert inputs == original
    assert result['bundle']['inventory']['packaging'] == core['packaging']
    assert 'six' in result['desired_diff'] and result['desired_diff'] == result['runtime_diff']
    retained = builder.store / result['bundle']['digest']
    verify_retained(retained, result['bundle']['digest'])
    (Path('/tmp/proof') / 'attestation.json').write_text(json.dumps(result, indent=2, sort_keys=True))
    print('PASS pinned uv 0.12.21: solve, source build, exact core, retained sources/wheels/inventory, cold-cache offline frozen --no-build disposable sync', flush=True)
    assert_conflict_rejected(builder, inputs)
    assert inputs == original
    print('PASS conflict: redacted failure, no input/runtime mutation', flush=True)
    unsupported = copy.deepcopy(inputs)
    unsupported['requirement']['declaration'] = 'six @ https://user:token@example.invalid/a?secret=value'
    try:
        builder.build(unsupported)
    except PreparationError as error:
        assert 'token' not in str(error) and 'secret' not in str(error)
    else:
        raise AssertionError('direct source accepted')
    print('PASS unsupported credential-bearing direct source rejected, diagnostic redacted; credential injection/cleanup NOT qualified', flush=True)
    wheel = next((retained / 'wheels').glob('*.whl'))
    contents = wheel.read_bytes()
    wheel.write_bytes(contents + b'corruption')
    try:
        verify_retained(retained, result['bundle']['digest'])
    except PreparationError:
        pass
    else:
        raise AssertionError('corruption accepted')
    wheel.write_bytes(contents)
    verify_retained(retained, result['bundle']['digest'])
    print('PASS retained-byte corruption rejected; reusable cache not relied upon', flush=True)


if __name__ == '__main__':
    main()
