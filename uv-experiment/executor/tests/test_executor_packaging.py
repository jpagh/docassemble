"""Build the executor in the fixture Docker COPY layout, not the source tree."""
import os
import sys
from pathlib import Path
import shutil
import subprocess


SOURCE = Path(__file__).resolve().parents[3]


def test_fixture_copy_layout_wheel_and_isolated_import(tmp_path):
    root = tmp_path / 'image'
    dockerfile = SOURCE / 'uv-experiment/fixture/Dockerfile'
    # Replay the actual relevant COPY instructions so layout changes are tested.
    for line in dockerfile.read_text().splitlines():
        if not line.startswith('COPY '):
            continue
        parts = line.split()
        if len(parts) != 3:
            continue
        _, source, destination = parts
        if source not in ('uv-experiment/executor',
                          'docassemble_base/docassemble/base/managed_records.py',
                          'docassemble_base/docassemble/base/managed_preparation.py',
                          'docassemble_base/docassemble/base/managed_adoption.py',
                          'docassemble_base/docassemble/base/managed_migration.py',
                          'docassemble_base/docassemble/base/managed_publication.py',
                          'docassemble_base/docassemble/base/managed_checkpoint.py',
                          'docassemble_base/docassemble/base/managed_release.py',
                          'docassemble_base/docassemble/base/managed_rollback.py',
                          'docassemble_base/docassemble/base/managed_config.py',
                          'docassemble_base/docassemble/base/managed_git.py',
                          'docassemble_base/docassemble/base/managed_direct.py',
                          'docassemble_base/docassemble/base/managed_artifacts.py',
                          'docassemble_base/docassemble/base/managed_qualification.py',
                          'docassemble_base/docassemble/base/managed_python.py',
                          'docassemble_base'):
            continue
        target = root / destination.lstrip('/')
        if (SOURCE / source).is_dir():
            shutil.copytree(SOURCE / source, target,
                            ignore=shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache'))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / source, target)
    project = root / 'opt/docassemble-executor'
    wheels = tmp_path / 'wheels'
    uv = shutil.which('uv')
    assert uv is not None
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('UV_PROJECT_ENVIRONMENT', None)
    subprocess.run([uv, 'build', '--wheel', '--out-dir', str(wheels), str(project)],
                   check=True, env=env, cwd=tmp_path)
    venv = tmp_path / 'isolated'
    subprocess.run([uv, 'venv', '--python', sys.executable, str(venv)], check=True, env=env)
    python = venv / 'bin/python'
    subprocess.run([uv, 'pip', 'install', '--python', str(python),
                    str(next(wheels.glob('*.whl')))], check=True, env=env)
    subprocess.run([str(python), '-I', '-c',
                    'from docassemble_executor.preparation import Preparation; '
                    'from docassemble.base.managed_python import python_target; '
                    'assert python_target()["python"] == ".".join(map(str, __import__("sys").version_info[:2])); '
                    'from docassemble.base.managed_records import preparation_tables; '
                    'from docassemble.base.managed_config import dependency_snapshot; '
                    'from docassemble.base.managed_artifacts import verify_artifact; '
                    'from docassemble.base.managed_git import parse_git_requirement; '
                    'from docassemble.base.managed_direct import parse_direct_requirement; '
                    'from docassemble.base.managed_migration import ImageMigration; '
                    'from docassemble_executor.image_migration import observe_migration; '
                    'from docassemble_executor.direct_sources import resolve_direct_sources; '
                    'from docassemble_executor.git_sources import resolve_sources; '
                    'assert dependency_snapshot({}, environ={})["requirements"] == []; '
                    'from docassemble_executor.installation import InstallationPoller; '
                    'from docassemble_executor.checkpoint_restore import require_restore_ready; '
                    'from docassemble.base.managed_checkpoint import package_projection; '
                    'from docassemble.base.managed_qualification import bridge_http_path; '
                    'from docassemble.base.managed_release import coherent_core; '
                    'from docassemble.base.managed_rollback import result_for; '
                    'from docassemble_executor.rollback_schema import retained_schema_policy; '
                    'from docassemble_executor.registry_builder import RegistryBuilder; '
                    'from docassemble_executor.release_catalog import PublicIndexCatalog; '
                    'assert bridge_http_path("/package_preview"); '
                    'from docassemble_executor.cli import build_parser; '
                    'import sys; '
                    'assert not any(n == "flask" or n.startswith(("celery", "redis", "docassemble.webapp", "docassemble.base.config")) for n in sys.modules); '
                    'assert build_parser().parse_args(["poll-installation", "--store", "/store", "--baseline", "/baseline", "--once"]).once; '
                    'assert len(preparation_tables()) == 14; '
                    'assert "retry_result" in preparation_tables(); '
                    'print("isolated executor/schema import: 14 tables")'],
                   check=True, env=env, cwd=tmp_path)
