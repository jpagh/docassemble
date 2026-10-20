"""Independent adoption source provenance and operator storage boundaries."""
import json
import os
from types import SimpleNamespace

import pytest

from docassemble.base.managed_adoption import AdoptionBlocked
from docassemble.base.managed_preparation import PreparationError
from docassemble_executor.legacy_adoption import historical_git_sha, worker_root

SOURCE = {'repository': 'https://example.org/repo', 'subdirectory': None, 'declared_ref': 'main'}


def metadata(runtime, directory='example-1.dist-info', **changes):
    path = runtime / 'lib/python3.12/site-packages' / directory
    path.mkdir(parents=True)
    (path / 'METADATA').write_text('Name: Example\nVersion: 1\n')
    document = {'url': SOURCE['repository'], 'vcs_info': {'vcs': 'git', 'requested_revision': 'main', 'commit_id': 'a' * 40}}
    document.update(changes)
    (path / 'direct_url.json').write_text(json.dumps(document))


def test_historical_git_commit_uses_metadata_not_installed_version_or_directory_name(tmp_path):
    metadata(tmp_path, directory='unrelated-directory.dist-info', url='https://user:PAT@example.org/repo')
    assert historical_git_sha(tmp_path, 'example', SOURCE) == 'a' * 40


@pytest.mark.parametrize('change', [
    {'url': 'https://example.org/different-repository'}, {'subdirectory': 'different'},
    {'vcs_info': {'vcs': 'git', 'requested_revision': 'other', 'commit_id': 'a' * 40}},
    {'vcs_info': {'vcs': 'git', 'requested_revision': 'main', 'commit_id': 'not-a-sha'}},
    {'vcs_info': {'vcs': 'hg', 'requested_revision': 'main', 'commit_id': 'a' * 40}}])
def test_historical_git_commit_requires_matching_declared_provenance(tmp_path, change):
    metadata(tmp_path, **change)
    assert historical_git_sha(tmp_path, 'example', SOURCE) is None


def test_conflicting_installed_git_commits_block_instead_of_choosing_inventory(tmp_path):
    metadata(tmp_path)
    metadata(tmp_path, directory='example-2.dist-info', vcs_info={'vcs': 'git', 'requested_revision': 'main', 'commit_id': 'b' * 40})
    with pytest.raises(AdoptionBlocked, match='ambiguous installed Git commits'):
        historical_git_sha(tmp_path, 'example', SOURCE)


@pytest.mark.parametrize('case', ['valid', 'unset', 'relative', 'wrong-owner', 'group-readable', 'symlink', 'traversal'])
def test_executor_store_is_explicit_restricted_worker_owned_without_api_root_weakening(tmp_path, monkeypatch, case):
    monkeypatch.setattr('docassemble_executor.legacy_adoption.pwd.getpwnam', lambda _: SimpleNamespace(pw_uid=os.getuid()))
    root = tmp_path / 'store'
    root.mkdir(mode=0o700)
    monkeypatch.setenv('DA_EXECUTOR_ARTIFACT_ROOT', str(root))
    if case == 'unset':
        monkeypatch.delenv('DA_EXECUTOR_ARTIFACT_ROOT')
    elif case == 'relative':
        monkeypatch.setenv('DA_EXECUTOR_ARTIFACT_ROOT', 'store')
    elif case == 'wrong-owner':
        monkeypatch.setattr('docassemble_executor.legacy_adoption.pwd.getpwnam', lambda _: SimpleNamespace(pw_uid=os.getuid() + 1))
    elif case == 'group-readable':
        root.chmod(0o750)
    elif case == 'symlink':
        alias = tmp_path / 'alias'
        alias.symlink_to(root)
        monkeypatch.setenv('DA_EXECUTOR_ARTIFACT_ROOT', str(alias))
    elif case == 'traversal':
        monkeypatch.setenv('DA_EXECUTOR_ARTIFACT_ROOT', str(root / '..' / 'store'))
    if case == 'valid':
        assert worker_root('DA_EXECUTOR_ARTIFACT_ROOT') == root
    else:
        with pytest.raises(PreparationError):
            worker_root('DA_EXECUTOR_ARTIFACT_ROOT')


def test_historical_git_resolution_preserves_verified_sha_without_advancing_branch(tmp_path, monkeypatch):
    from docassemble_executor.legacy_adoption import resolve_git
    metadata(tmp_path)
    monkeypatch.setenv('DA_RUNTIME_VENV', str(tmp_path))
    monkeypatch.setattr('docassemble_executor.legacy_adoption.resolve_source', lambda source, *args, **kwargs:
        dict(source, ref_kind='branch', resolved_sha='b' * 40))
    assert resolve_git('example', SOURCE, None)['resolved_sha'] == 'a' * 40


def test_rendered_configuration_null_overlay_is_absent_not_unresolved_intent():
    from docassemble.base.managed_adoption import configuration_intent
    policy, observed, rows = configuration_intent({'config from': None, 'python packages': ['Example>=2']}, {},
        {'config': {'index_url': 'https://pypi.org/simple'}})
    assert policy == {'index_url': 'https://pypi.org/simple'}
    assert rows[0]['declaration'] == 'Example>=2'
