from docassemble_executor.bundles import hash_project, verify_bundle


def make_bundle(directory, lock="lock-contents"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (directory / "uv.lock").write_text(lock, encoding="utf-8")
    return directory


def test_hash_project_changes_with_lock(tmp_path):
    bundle = make_bundle(tmp_path / "core-r1")
    first = hash_project(bundle)
    (bundle / "uv.lock").write_text("different", encoding="utf-8")
    assert hash_project(bundle) != first


def test_verify_bundle(tmp_path):
    bundle = make_bundle(tmp_path / "core-r1")
    manifest = {"bundles": {"core-r1": hash_project(bundle)}}
    ok, detail = verify_bundle(bundle, manifest, "core-r1")
    assert ok, detail
    (bundle / "uv.lock").write_text("tampered", encoding="utf-8")
    ok, detail = verify_bundle(bundle, manifest, "core-r1")
    assert not ok
    assert "mismatch" in detail
    ok, detail = verify_bundle(bundle, manifest, "core-r2")
    assert not ok
    assert "not recorded" in detail
