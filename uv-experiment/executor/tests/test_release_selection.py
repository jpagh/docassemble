"""Coherent core release selection, catalog and builder upgrade-mode tests."""
from docassemble.base.managed_python import python_requires, python_target
import io
import json
import tarfile
import zipfile
from types import SimpleNamespace

import pytest

import docassemble_executor.registry_builder as registry_module
from docassemble_executor.preparation import PreparationError
from docassemble_executor.registry_builder import RegistryBuilder
from docassemble_executor.release_catalog import PublicIndexCatalog
from docassemble.base.managed_release import (coherent_core, core_requirements, parse_release_selection,
                                              reject_downgrade, select_release, upgrade_risk_disclosure)

INDEX = 'https://pypi.org/simple'
TARGET = python_target()
TOOL = {'uv': '0.12.21'}
PIN = {'version': '1.0.0', 'source_digest': 'a' * 64}


def _wheel(name, version, requires):
    dist = name.replace('-', '_') + '-' + version + '.dist-info'
    metadata = ('Metadata-Version: 2.1\nName: ' + name + '\nVersion: ' + version + '\n' +
                ''.join('Requires-Dist: ' + value + '\n' for value in requires))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(dist + '/METADATA', metadata)
        archive.writestr(dist + '/WHEEL', 'Wheel-Version: 1.0\n')
    return buffer.getvalue()


def _sdist(name, version, requires):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        metadata = ('Metadata-Version: 2.1\nName: ' + name + '\nVersion: ' + version + '\n' +
                    ''.join('Requires-Dist: ' + value + '\n' for value in requires)).encode()
        info = tarfile.TarInfo(name.replace('-', '_') + '-' + version + '/PKG-INFO')
        info.size = len(metadata)
        archive.addfile(info, io.BytesIO(metadata))
    return buffer.getvalue()


# --------------------------------------------------------------- selection


def test_parse_release_selection_modes_and_rejections():
    assert parse_release_selection({'mode': 'latest-stable'}).to_dict() == {'mode': 'latest-stable', 'version': None}
    assert parse_release_selection({'mode': 'exact', 'version': '1.2.3'}).version == '1.2.3'
    assert parse_release_selection({'mode': 'prerelease', 'version': '2.0.0b1'}).version == '2.0.0b1'
    for bad in (None, {}, {'mode': 'other'}, {'mode': 'latest-stable', 'version': '1.0'},
                {'mode': 'exact'}, {'mode': 'exact', 'version': 'garbage'},
                {'mode': 'exact', 'version': '1.0', 'extra': 1}):
        with pytest.raises(PreparationError):
            parse_release_selection(bad)


def test_select_release_latest_stable_skips_prereleases_without_fallback():
    selection = parse_release_selection({'mode': 'latest-stable'})
    assert select_release(['1.0.0', '2.0.0b1', '1.5.0'], selection) == '1.5.0'
    assert select_release(['1.0.0'], selection) == '1.0.0'
    with pytest.raises(PreparationError, match='no stable'):
        select_release(['2.0.0b1'], selection)
    with pytest.raises(PreparationError, match='no registry releases'):
        select_release([], selection)


def test_select_release_exact_and_prerelease_are_explicit_and_exact():
    assert select_release(['1.0.0', '1.2.0'], parse_release_selection(
        {'mode': 'exact', 'version': '1.0.0'})) == '1.0.0'
    with pytest.raises(PreparationError, match='not published'):
        select_release(['1.0.0'], parse_release_selection({'mode': 'exact', 'version': '9.9.9'}))
    with pytest.raises(PreparationError, match='prereleases require explicit'):
        select_release(['2.0.0b1'], parse_release_selection({'mode': 'exact', 'version': '2.0.0b1'}))
    with pytest.raises(PreparationError, match='requires a prerelease'):
        select_release(['1.0.0'], parse_release_selection({'mode': 'prerelease', 'version': '1.0.0'}))
    assert select_release(['1.0.0', '2.0.0b1'], parse_release_selection(
        {'mode': 'prerelease', 'version': '2.0.0b1'})) == '2.0.0b1'


def test_coherent_core_requires_exact_matching_base_and_demo_pins():
    assert coherent_core('1.2.0', ['docassemble.base==1.2.0', 'flask>=3', 'docassemble.demo==1.2.0']) == {
        'docassemble-webapp': '1.2.0', 'docassemble-base': '1.2.0', 'docassemble-demo': '1.2.0'}
    for bad in (
            ['docassemble.base==1.2.0'],                                     # missing demo
            ['docassemble.base>=1.2.0', 'docassemble.demo==1.2.0'],          # non-exact base
            ['docassemble.base==1.1.0', 'docassemble.demo==1.2.0'],          # inconsistent base
            ['docassemble.base==1.2.0', 'docassemble.demo==1.1.0'],          # inconsistent demo
            ['docassemble.base==1.2.0', 'docassemble.demo==1.2.0', 'docassemble.base==1.2.0'],  # duplicate
            ['docassemble.base @ https://files.example/docassemble_base-1.2.0.whl',
             'docassemble.demo==1.2.0'],                                     # independent Git/URL core
            ['docassemble.base==1.2.0; extra == "x"', 'docassemble.demo==1.2.0'],  # marker
            ['not a requirement', 'docassemble.demo==1.2.0'],
    ):
        with pytest.raises(PreparationError):
            coherent_core('1.2.0', bad)
    assert core_requirements({'docassemble-webapp': '1.2.0', 'docassemble-base': '1.2.0',
                              'docassemble-demo': '1.2.0'}) == [
        'docassemble-base==1.2.0', 'docassemble-webapp==1.2.0', 'docassemble-demo==1.2.0']


@pytest.mark.parametrize('version,rejected', [('0.9.0', True), ('1.0.0', True), ('1.1.0', False)])
def test_reject_downgrade_only_allows_strictly_newer(version, rejected):
    current = {'docassemble-webapp': {'version': '1.0.0', 'source_digest': 'a' * 64}}
    coherent = {'docassemble-webapp': version, 'docassemble-base': version, 'docassemble-demo': version}
    if rejected:
        with pytest.raises(PreparationError, match='fresh downgrade is unsupported'):
            reject_downgrade(coherent, current)
    else:
        reject_downgrade(coherent, current)
    reject_downgrade(coherent, {})  # no comparable current core is not a downgrade


# ----------------------------------------------------------------- catalog


def _catalog(files, payloads):
    def fetch(url, headers):
        if url in payloads:
            return payloads[url]
        return json.dumps({'files': files}).encode()
    return PublicIndexCatalog(INDEX, fetch=fetch)


def test_catalog_reads_pep691_versions_and_wheel_metadata():
    url = 'https://files.pythonhosted.org/packages/docassemble_webapp-1.1.0-py3-none-any.whl'
    catalog = _catalog([{'filename': 'docassemble_webapp-1.1.0-py3-none-any.whl', 'url': url},
                        {'filename': 'docassemble_webapp-2.0.0b1-py3-none-any.whl',
                         'url': 'https://files.pythonhosted.org/packages/beta.whl'}],
                       {url: _wheel('docassemble.webapp', '1.1.0',
                                    ['docassemble.base==1.1.0', 'docassemble.demo==1.1.0'])})
    assert catalog.versions('docassemble.webapp') == ['1.1.0', '2.0.0b1']
    assert catalog.requires_dist('docassemble.webapp', '1.1.0') == [
        'docassemble.base==1.1.0', 'docassemble.demo==1.1.0']


def test_catalog_reads_sdist_metadata_and_rejects_unrecognized_index():
    url = 'https://files.pythonhosted.org/packages/docassemble_webapp-1.1.0.tar.gz'
    catalog = _catalog([{'filename': 'docassemble_webapp-1.1.0.tar.gz', 'url': url}],
                       {url: _sdist('docassemble.webapp', '1.1.0', ['docassemble.base==1.1.0'])})
    assert catalog.requires_dist('docassemble.webapp', '1.1.0') == ['docassemble.base==1.1.0']
    broken = PublicIndexCatalog(INDEX, fetch=lambda url, headers: b'<html>not json</html>')
    with pytest.raises(PreparationError, match='PEP 691'):
        broken.versions('docassemble.webapp')
    with pytest.raises(PreparationError, match='approved HTTPS index'):
        PublicIndexCatalog('http://insecure/simple')


# -------------------------------------------------------------- builder mode


class FakeCatalog:
    def __init__(self, versions, requires):
        self._versions, self._requires = versions, requires

    def versions(self, project):
        return list(self._versions)

    def requires_dist(self, project, version):
        return list(self._requires[version])


def upgrade_inputs(*, selection=None, extra_contributions=None, core=None):
    return {
        'baseline': {'target': TARGET, 'tool': TOOL, 'config': {'index_url': INDEX},
                     'core': core if core is not None else {'core': PIN},
                     'desired_inventory': {'core': PIN},
                     'runtime': {'kind': 'normal', 'inventory': {'core': PIN}}},
        'change_operation': 'upgrade', 'ownership_effects': {},
        'release_selection': selection or {'mode': 'exact', 'version': '2.0.0'},
        'contributions': list(extra_contributions or []),
    }


def _bundle(inventory, digest_letter, artifacts):
    return {'digest': digest_letter * 64, 'project_digest': digest_letter * 64,
            'lock_digest': digest_letter * 64, 'inventory': inventory, 'artifacts': artifacts}


def _diff(before, after):
    return {name: {'before': before.get(name), 'after': after.get(name)}
            for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)}


def _prepare_builder(tmp_path, monkeypatch, calls, *, fail_full=False):
    monkeypatch.setattr(registry_module.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(registry_module.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=b'uv 0.12.21'))
    requires = {'2.0.0': ['docassemble.base==2.0.0', 'docassemble.demo==2.0.0', 'flask>=3']}
    builder = RegistryBuilder(tmp_path / 'store', release_catalog=FakeCatalog(['1.0.0', '2.0.0'], requires))

    def solve(inputs, *, operation, core, requirements, validation=None, **kwargs):
        calls.append({'operation': operation, 'core': core, 'requirements': list(requirements),
                      'inputs': inputs, 'image_wheels': kwargs.get('image_wheels'),
                      'image_registry_wheels': kwargs.get('image_registry_wheels')})
        if operation == 'upgrade-core':
            inventory = {'docassemble-base': {'version': '2.0.0', 'source_digest': '1' * 64},
                         'docassemble-webapp': {'version': '2.0.0', 'source_digest': '2' * 64},
                         'docassemble-demo': {'version': '2.0.0', 'source_digest': '3' * 64},
                         'flask': {'version': '3.1.0', 'source_digest': '4' * 64},
                         'gunicorn': {'version': '23.0.0', 'source_digest': '5' * 64}}
            from docassemble_executor.preparation import digest
            from docassemble_executor.bundles import sha256_file
            directory = builder.store / 'staging-core'
            (directory / 'wheels').mkdir(parents=True)
            for name, pin in inventory.items():
                # Distinct payloads model an sdist whose second build would differ.
                (directory / 'wheels' / (name.replace('-', '_') + '-' + pin['version'] + '-py3-none-any.whl')).write_bytes(
                    b'first-build-only:' + name.encode())
            (directory / 'inventory.json').write_text(json.dumps(inventory))
            (directory / 'pyproject.toml').write_text('[project]\nrequires-python=' + json.dumps(python_requires()))
            (directory / 'uv.lock').write_text('requires-python=' + json.dumps(python_requires()))
            manifest = {p.relative_to(directory).as_posix(): sha256_file(p) for p in directory.rglob('*') if p.is_file()}
            core_digest = digest(manifest)
            (directory / 'manifest.json').write_text(json.dumps(manifest))
            directory.rename(builder.store / core_digest)
            bundle = dict(_bundle(inventory, 'a', ['9' * 64]), digest=core_digest,
                          project_digest=manifest['pyproject.toml'], lock_digest=manifest['uv.lock'])
            baseline = inputs['baseline']
            return {'bundle': bundle,
                    'desired_diff': _diff(baseline['desired_inventory'], inventory),
                    'runtime_diff': _diff(baseline['runtime']['inventory'], inventory),
                    'warnings': [], 'ownership_effects': {},
                    'validation': {'core_unchanged': True, 'requirements_satisfied': True,
                                   'durable_materialization_verified': True}}
        if fail_full:
            raise PreparationError('registry preparation command failed')
        inventory = {**core, 'example': {'version': '3.0', 'source_digest': 'e' * 64}}
        baseline = inputs['baseline']
        return {'bundle': _bundle(inventory, 'd', ['e' * 64]),
                'desired_diff': _diff(baseline['desired_inventory'], inventory),
                'runtime_diff': _diff(baseline['runtime']['inventory'], inventory),
                'warnings': [], 'ownership_effects': {}, 'validation': validation}

    monkeypatch.setattr(builder, '_solve', solve)
    return builder


def test_builder_upgrade_solves_core_then_full_and_discloses_risk(tmp_path, monkeypatch):
    calls = []
    builder = _prepare_builder(tmp_path, monkeypatch, calls)
    result = builder.build(upgrade_inputs(extra_contributions=[{'id': 'x', 'declaration': 'example>=1'}]))
    assert [call['operation'] for call in calls] == ['upgrade-core', 'upgrade']
    assert calls[0]['requirements'] == core_requirements(
        {'docassemble-webapp': '2.0.0', 'docassemble-base': '2.0.0', 'docassemble-demo': '2.0.0'}) + ['gunicorn']
    assert calls[1]['requirements'] == [
        'example>=1', 'docassemble-base==2.0.0', 'docassemble-webapp==2.0.0', 'docassemble-demo==2.0.0',
        'flask==3.1.0', 'gunicorn==23.0.0']
    assert set(calls[1]['image_wheels']) == set(result['core_bundle']['inventory'])
    assert all(path.read_bytes() == b'first-build-only:' + name.encode()
               for name, path in calls[1]['image_wheels'].items())
    assert calls[1]['core'] == result['core_bundle']['inventory']
    assert result['release'] == {'selection': {'mode': 'exact', 'version': '2.0.0'}, 'resolved': '2.0.0',
                                 'webapp': '2.0.0', 'base': '2.0.0', 'demo': '2.0.0',
                                 'core_bundle': result['core_bundle']['digest']}
    assert result['risk_disclosure'] == upgrade_risk_disclosure()
    assert result['validation'] == {'core_matches_release': True, 'requirements_satisfied': True,
                                    'durable_materialization_verified': True}
    assert set(result) == {'bundle', 'core_bundle', 'release', 'desired_diff', 'runtime_diff', 'warnings',
                           'validation', 'ownership_effects', 'risk_disclosure'}
    assert set(result['bundle']['inventory']) >= set(result['core_bundle']['inventory'])


def test_builder_upgrade_rejects_conflict_failure_without_weakening(tmp_path, monkeypatch):
    calls = []
    builder = _prepare_builder(tmp_path, monkeypatch, calls, fail_full=True)
    with pytest.raises(PreparationError, match='preparation command failed'):
        builder.build(upgrade_inputs())
    assert [call['operation'] for call in calls] == ['upgrade-core', 'upgrade']


def test_builder_upgrade_rejects_incoherent_metadata_before_solving(tmp_path, monkeypatch):
    calls = []
    builder = _prepare_builder(tmp_path, monkeypatch, calls)
    builder.release_catalog = FakeCatalog(['2.0.0'], {'2.0.0': ['docassemble.base==2.0.0']})
    with pytest.raises(PreparationError, match='matching base and demo'):
        builder.build(upgrade_inputs())
    assert calls == []


def test_builder_upgrade_rejects_downgrade_and_private_policy(tmp_path, monkeypatch):
    calls = []
    builder = _prepare_builder(tmp_path, monkeypatch, calls)
    baseline = {'docassemble-webapp': {'version': '3.0.0', 'source_digest': 'a' * 64},
                'core': {'version': '1.0.0', 'source_digest': 'a' * 64}}
    with pytest.raises(PreparationError, match='fresh downgrade is unsupported'):
        builder.build(upgrade_inputs(selection={'mode': 'exact', 'version': '2.0.0'}, core=baseline))
    assert calls == []


def test_builder_upgrade_runs_under_qualified_image_baseline(tmp_path, monkeypatch):
    calls = []
    sentinel = object()
    builder = _prepare_builder(tmp_path, monkeypatch, calls)
    builder.image_baseline = tmp_path / 'image-baseline.json'
    inputs = upgrade_inputs()
    inputs['baseline']['image_baseline'] = {'image_target': 'fixture', 'bundle': 'core-r1',
                                            'bundle_digest': 'a' * 64}
    monkeypatch.setattr('docassemble_executor.image_baseline.load_image_registry_wheels', lambda *a: {'numpy': sentinel})
    monkeypatch.setattr('docassemble_executor.image_baseline.load_image_wheels',
                        lambda *args: (inputs['baseline'], {}))
    result = builder.build(inputs)
    assert [call['operation'] for call in calls] == ['upgrade-core', 'upgrade']
    assert calls[0]['image_wheels'] == {}  # Fresh solve, not the old image core.
    assert set(calls[1]['image_wheels']) == set(result['core_bundle']['inventory'])
    assert result['release']['webapp'] == '2.0.0'
    assert calls[0]['image_registry_wheels'] == calls[1]['image_registry_wheels'] == {'numpy': sentinel}
