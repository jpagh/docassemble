"""Managed backup object enumeration and safe copy boundaries (no DB/client required)."""
import hashlib

import pytest

from docassemble_executor.managed_backup import (
    ManagedBackupError, ObjectCopy, _copy_value, canonical_records, references)


def records(snapshot=None, materials=None):
    return {'revision': [{'id': 'old', 'snapshot': snapshot or {}}],
            'environment_state': [], 'requirement_contribution': [],
            'candidate_material': materials or []}


def test_all_retained_references_not_just_current_and_no_candidate_only_bytes():
    value = records({'publication': {'result': {'bundle': {'digest': 'a' * 64}}},
                     'selected_baseline': {'core_bundle': {'digest': 'b' * 64}},
                     'contributions': [{'artifact_ref': 'c' * 64}],
                     'direct_sources': {'direct': {'digest': 'd' * 64}},
                     'config': {'private_indexes': {'private': 'https://example.test/'}}},
                    [{'id': 'candidate', 'committed': False, 'path': '/do-not-copy'}])
    refs = references(canonical_records(value))
    assert refs == {'bundles': ['a' * 64, 'b' * 64], 'artifacts': ['c' * 64, 'd' * 64],
                    'material_paths': [], 'indexes': ['private'], 'image_bundles': {}}


@pytest.mark.parametrize('missing', [True, False])
def test_missing_or_deleted_persistent_git_authentication_fails(missing):
    rows = [] if missing else [{'id': 'credential', 'committed': True, 'deleted': True, 'path': '/secret'}]
    with pytest.raises(ManagedBackupError, match='authentication'):
        references(records({'git_credentials': {'git': 'credential'}}, rows))


@pytest.mark.parametrize('value', ['../bad', '', 'A' * 64])
def test_untrusted_digest_cannot_escape_store(value):
    with pytest.raises(ManagedBackupError, match='digest'):
        references(records({'selected_baseline': {'core_bundle': {'digest': value}}}))


def test_copy_records_bytes_modes_and_original_restore_destination(tmp_path):
    source = tmp_path / 'PAT'
    source.write_bytes(b'https://fixture:PAT@example.test/repo')
    source.chmod(0o600)
    copy = ObjectCopy(tmp_path / 'backup')
    copy.file(source, 'secret')
    assert copy.files['secret']['source'] == str(source)
    assert copy.files['secret']['mode'] == 0o600
    assert copy.files['secret']['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (tmp_path / 'backup/objects/secret').read_bytes() == source.read_bytes()


def test_symlink_and_parent_symlink_never_copy(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'file').write_text('secret')
    (tmp_path / 'link').symlink_to(source, target_is_directory=True)
    with pytest.raises(ManagedBackupError, match='regular'):
        ObjectCopy(tmp_path / 'backup').file(tmp_path / 'link/file', 'secret')


def test_copy_text_round_trips_json_backslashes_tabs_unicode_and_null():
    assert _copy_value(r'\\') == '\\'
    assert _copy_value(r'\t') == '\t'
    assert _copy_value('é') == 'é'
    assert _copy_value(r'\N') is None
    assert _copy_value(r'\\N') == r'\N'


def test_historical_image_bundle_identity_is_mandatory():
    refs = references(records({'image_baseline': {'bundle': 'runtime-old', 'bundle_digest': 'a' * 64}}))
    assert refs['image_bundles'] == {'a' * 64: ['runtime-old']}


def test_local_recovery_selection_is_also_mandatory():
    assert references(records(), {'recovery_baseline': {'core_bundle': {'digest': 'a' * 64}}})['bundles'] == ['a' * 64]


def test_image_source_build_caches_excluded_but_manifest_retained_bytes_not_filtered(tmp_path):
    source = tmp_path / 'source'
    (source / 'build').mkdir(parents=True)
    (source / 'build/disposable').write_text('cache')
    (source / 'required').write_text('mandatory')
    image = ObjectCopy(tmp_path / 'image-backup')
    image.tree(source, 'image', exclude_disposable=True)
    assert set(image.files) == {'image/required'}
    retained = ObjectCopy(tmp_path / 'retained-backup')
    retained.tree(source, 'retained')
    assert set(retained.files) == {'retained/required', 'retained/build/disposable'}
