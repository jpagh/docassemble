"""Browser/API and registry proof dispatch selects validated worker-only storage."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from docassemble_executor.preparation import PreparationError

PREPARATION = Path(__file__).resolve().parents[2] / 'preparation'


def load(name, monkeypatch):
    monkeypatch.syspath_prepend(str(PREPARATION))
    spec = importlib.util.spec_from_file_location(name, PREPARATION / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('proof_name', ['image_bridge_proof', 'registry_changes_image_proof'])
@pytest.mark.parametrize('worker_config', ['valid', 'unset', 'web-ingress', 'wrong-owner', 'group-readable'])
def test_proof_preparation_command_uses_validated_worker_root_not_web_ingress(
        monkeypatch, tmp_path, proof_name, worker_config):
    proof = load(proof_name, monkeypatch)
    web = tmp_path / 'docassemble/artifacts'
    worker = tmp_path / 'docassemble-build/artifacts'
    web.mkdir(parents=True, mode=0o700)
    worker.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv('DA_MANAGED_ARTIFACT_ROOT', str(web))
    monkeypatch.setenv('DA_MANAGED_ARTIFACT_UID', '33')
    monkeypatch.setenv('DA_EXECUTOR_ARTIFACT_ROOT', str(worker))
    monkeypatch.setattr('docassemble_executor.legacy_adoption.pwd.getpwnam',
                        lambda _: SimpleNamespace(pw_uid=os.getuid()))
    # Model the web writer's disjoint UID without requiring privileged chown.
    web_uid = 33 if os.getuid() != 33 else 34
    original_stat = Path.stat
    def stat(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        if path == web:
            fields = list(info)
            fields[4] = web_uid
            return os.stat_result(fields)
        return info
    monkeypatch.setattr(Path, 'stat', stat)
    if worker_config == 'unset':
        monkeypatch.delenv('DA_EXECUTOR_ARTIFACT_ROOT')
    elif worker_config == 'web-ingress':
        monkeypatch.setenv('DA_EXECUTOR_ARTIFACT_ROOT', str(web))
    elif worker_config == 'wrong-owner':
        monkeypatch.setattr('docassemble_executor.legacy_adoption.pwd.getpwnam',
                            lambda _: SimpleNamespace(pw_uid=os.getuid() + 1))
    elif worker_config == 'group-readable':
        worker.chmod(0o750)
    if worker_config != 'valid':
        with pytest.raises(PreparationError):
            proof.preparation_command()
        return

    assert web.stat().st_uid != worker.stat().st_uid
    assert web.stat().st_mode & 0o777 == worker.stat().st_mode & 0o777 == 0o700
    command = proof.preparation_command()
    assert command == ['runuser', '-u', 'da-environment', '--', proof.sys.executable, '-m',
                       'docassemble_executor.preparation_poller', '--store', str(proof.STORE),
                       '--image-baseline', str(proof.DESCRIPTOR), '--artifact-root', str(worker), '--once']
    assert str(web) not in command
