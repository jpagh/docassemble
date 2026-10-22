from docassemble.base.managed_python import python_site_packages
from docassemble_executor.alembic_probe import (
    STATUS_COMPATIBLE,
    STATUS_INCOMPATIBLE,
    collect_revisions,
    decide_compatibility,
    find_versions_dir,
)


def write_migration(directory, name, revision, down_revision):
    down = "None" if down_revision is None else repr(down_revision)
    (directory / name).write_text(
        "revision = " + repr(revision) + "\n"
        "down_revision = " + down + "\n"
        "def upgrade():\n    pass\n",
        encoding="utf-8",
    )


def test_collect_revisions_handles_strings_tuples_and_broken_files(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    write_migration(versions, "a.py", "aaa", None)
    write_migration(versions, "b.py", "bbb", "aaa")
    (versions / "merge.py").write_text(
        "revision = 'ccc'\ndown_revision = ('bbb', 'ddd')\n", encoding="utf-8"
    )
    (versions / "broken.py").write_text("this is not python", encoding="utf-8")
    (versions / "dynamic.py").write_text("revision = SOME_VARIABLE\n", encoding="utf-8")
    revisions = collect_revisions(versions)
    assert revisions["aaa"] == ()
    assert revisions["bbb"] == ("aaa",)
    assert revisions["ccc"] == ("bbb", "ddd")
    assert "broken" not in revisions
    assert len(revisions) == 3


def test_decide_compatibility():
    known = {"aaa": (), "bbb": ("aaa",)}
    assert decide_compatibility("aaa", known)[0] == STATUS_COMPATIBLE
    assert decide_compatibility(None, known)[0] == STATUS_INCOMPATIBLE
    assert decide_compatibility("future", known)[0] == STATUS_INCOMPATIBLE


def test_find_versions_dir(tmp_path):
    venv = tmp_path / "venv"
    versions = venv / python_site_packages() / "docassemble" / "webapp" / "alembic" / "versions"
    versions.mkdir(parents=True)
    assert find_versions_dir(venv) == versions
    assert find_versions_dir(tmp_path / "missing") is None
