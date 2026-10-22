from docassemble.base.managed_python import python_site_packages
from docassemble_executor.health import runtime_files_present, runtime_python


def make_runtime(venv):
    packages = venv / python_site_packages()
    (packages / "docassemble" / "webapp").mkdir(parents=True)
    (packages / "docassemble" / "webapp" / "__init__.py").write_text("", encoding="utf-8")
    (packages / "docassemble" / "base").mkdir()
    (venv / "bin").mkdir(parents=True)
    runtime_python(venv).write_text("", encoding="utf-8")


def test_runtime_files_present(tmp_path):
    venv = tmp_path / "venv"
    assert not runtime_files_present(venv)
    make_runtime(venv)
    assert runtime_files_present(venv)
    (venv / "bin" / "python").unlink()
    assert not runtime_files_present(venv)
