"""Optional experiment-only nested service context; legacy behavior is unchanged."""
import os


def _support():
    # This experiment's support is image-owned and outside the runtime venv.
    # Setup/probe subprocesses may not inherit the serving PYTHONPATH.
    import sys
    if '/opt/docassemble-executor/src' not in sys.path:
        sys.path.insert(0, '/opt/docassemble-executor/src')
    from docassemble_executor import capabilities
    return capabilities


def accepted_caller():
    if os.environ.get('DA_UV_MANAGED') != 'true':
        return ''
    return _support().current()


def conversion_environment():
    if os.environ.get('DA_UV_MANAGED') != 'true':
        return None  # subprocess inherits exactly the legacy environment
    return _support().subprocess_env()


def conversion_path(legacy):
    if os.environ.get('DA_UV_MANAGED') != 'true':
        return legacy
    from pathlib import Path
    return str(Path(_support().__file__).with_name('convert_client.py'))
