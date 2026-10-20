import json

from docassemble_executor.config import database_settings, load_config
from docassemble_executor.state import AppliedState, DesiredState


def test_load_config_normalizes_underscores(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("db:\n  host: localhost\n  user: docassemble\nsome_key: value\n", encoding="utf-8")
    config = load_config(path)
    assert config["some key"] == "value"
    settings = database_settings(config)
    assert settings.host == "localhost"
    assert settings.user == "docassemble"
    assert settings.connect_kwargs["dbname"] == "docassemble"


def test_database_settings_defaults():
    settings = database_settings({})
    assert settings.host == "localhost"
    assert settings.name == "docassemble"
    assert settings.connect_kwargs == {"host": "localhost", "dbname": "docassemble"}


def test_desired_state_is_read_only(tmp_path):
    path = tmp_path / "desired.json"
    path.write_text(
        json.dumps({"revision": "runtime-r2", "core_baseline": "core-r1", "packages": ["sentinel"]}),
        encoding="utf-8",
    )
    desired = DesiredState.load(path)
    assert desired.revision == "runtime-r2"
    assert desired.packages == ("sentinel",)


def test_applied_state_round_trip(tmp_path):
    path = tmp_path / "applied.json"
    applied = AppliedState()
    applied.mark("runtime-r1", "core-r1")
    applied.save(path)
    loaded = AppliedState.load(path)
    assert loaded.revision == "runtime-r1"
    assert loaded.core_baseline == "core-r1"
    assert loaded.at is not None
