"""Confirmed startup seam: immutable input and durable boot-policy decisions."""

import pytest

from docassemble_executor.startup import StartupError, decide_startup, managed_requested


def test_converged_true_boot_needs_no_install():
    assert decide_startup('true', initialized=True, installed_valid=True, pending=False) == 'retain'


@pytest.mark.parametrize('mode', ['initial', 'false'])
def test_verified_startup_can_leave_desired_pending(mode):
    assert decide_startup(mode, initialized=True, installed_valid=True, pending=True) == 'retain'


def test_true_converges_pending_exact_lock():
    assert decide_startup('true', initialized=True, installed_valid=True, pending=True) == 'sync'


def test_initial_only_initializes_once():
    assert decide_startup('initial', initialized=False, installed_valid=False, pending=False) == 'sync'


def test_false_cannot_claim_fresh_initialization():
    with pytest.raises(StartupError, match='verified'):
        decide_startup('false', initialized=False, installed_valid=True, pending=False)


@pytest.mark.parametrize('mode', ['true', 'initial', 'false'])
def test_invalid_previously_initialized_runtime_requires_safety_recovery(mode):
    assert decide_startup(mode, initialized=True, installed_valid=False, pending=False) == 'recover'


def test_flag_off_without_evidence_preserves_legacy(tmp_path):
    assert not managed_requested(False, tmp_path)


@pytest.mark.parametrize('filename', ['desired.json', 'applied.json', 'lifecycle.json', 'initialized.json'])
def test_even_corrupt_durable_evidence_prevents_flag_off_escape(tmp_path, filename):
    (tmp_path / filename).write_text('broken')
    with pytest.raises(StartupError, match='disabled'):
        managed_requested(False, tmp_path)
