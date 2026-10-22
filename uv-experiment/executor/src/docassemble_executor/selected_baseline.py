"""Executor-owned selected/recovery core state, independent of the image manifest.

Selection is intent; recovery advances only after schema and all-role health
success. Retained bundles remain content-addressed and are reverified on use.
"""
from __future__ import annotations

import json
from pathlib import Path

from .bundles import sha256_file, verify_python_target
from .paths import ExecutorPaths
from .preparation import PreparationError
from .registry_builder import verify_retained
from .state import _atomic_write_json


def load_baselines(paths: ExecutorPaths) -> dict:
    path = paths.state_dir / 'published-baselines.json'
    return json.loads(path.read_text()) if path.exists() else {}


def retain_selection(paths: ExecutorPaths, snapshot: dict, store: Path) -> None:
    if snapshot.get('selected_baseline') or snapshot.get('schema_mode') == 'unchanged' or snapshot.get('migration'):
        _atomic_write_json(paths.state_dir / 'published-baselines.json', {
            'image_baseline': snapshot['image_baseline'],
            'selected_baseline': snapshot.get('selected_baseline'),
            'recovery_baseline': snapshot['recovery_baseline'],
            'store': str(store.resolve()),
            'schema_mode': snapshot.get('schema_mode', 'upgrade'),
            **({'migration': True} if snapshot.get('migration') else {}),
        })


def promote_recovery(paths: ExecutorPaths, snapshot: dict) -> None:
    if snapshot.get('selected_baseline') or snapshot.get('schema_mode') == 'unchanged' or snapshot.get('migration'):
        document = load_baselines(paths)
        if document.get('selected_baseline') != snapshot.get('selected_baseline'):
            raise PreparationError('selected baseline changed')
        document['recovery_baseline'] = snapshot.get('selected_baseline') or {
            'image_baseline': snapshot['image_baseline'], 'core': snapshot['core']}
        _atomic_write_json(paths.state_dir / 'published-baselines.json', document)


def selected_directory(store: Path, selected: dict) -> Path:
    bundle = selected['core_bundle']
    directory = store / bundle['digest']
    verify_retained(directory, bundle['digest'])
    if not verify_python_target(directory / 'pyproject.toml', directory / 'uv.lock'):
        raise PreparationError('selected core material changed')
    if (json.loads((directory / 'inventory.json').read_text()) != bundle['inventory'] or
            sha256_file(directory / 'pyproject.toml') != bundle['project_digest'] or
            sha256_file(directory / 'uv.lock') != bundle['lock_digest']):
        raise PreparationError('selected core material changed')
    return directory


def core_versions(selected: dict) -> dict[str, str]:
    return {name: value['version'] for name, value in selected['core_bundle']['inventory'].items()}


def release_versions(selected: dict) -> dict[str, str]:
    # probe_runtime reports only the three dotted distribution names, not the
    # canonicalized full dependency inventory used for exact material checks.
    return {'docassemble.' + name: selected['release'][name] for name in ('base', 'webapp', 'demo')}


def selected_wheels(store: Path, selected: dict) -> dict[str, Path]:
    from packaging.utils import canonicalize_name, parse_wheel_filename
    directory = selected_directory(store, selected)
    wheels = {canonicalize_name(parse_wheel_filename(path.name)[0]): path
              for path in (directory / 'wheels').glob('*.whl')}
    if set(wheels) != set(core_versions(selected)):
        raise PreparationError('incomplete selected core wheels')
    return wheels
