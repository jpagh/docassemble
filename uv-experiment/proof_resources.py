"""Collision checks and verified teardown for disposable proof resources."""


def resources(run):
    containers = set(run(['docker', 'ps', '-a', '--format', '{{.Names}}']).stdout.splitlines())
    volumes = set(run(['docker', 'volume', 'ls', '-q']).stdout.splitlines())
    return containers, volumes


def refuse_existing(run, *, containers=(), volumes=()):
    present_containers, present_volumes = resources(run)
    collisions = sorted(set(containers) & present_containers) + sorted(set(volumes) & present_volumes)
    if collisions:
        raise RuntimeError('pre-existing proof resources must be excluded first: ' + ', '.join(collisions))


def cleanup(run, *, containers=(), volumes=()):
    """Only callers' run-owned names may be supplied; verify they are gone."""
    failures = []
    present_containers, present_volumes = resources(run)
    for name in sorted(set(containers) & present_containers):
        if run(['docker', 'rm', '-f', name], check=False).returncode:
            failures.append(name)
    for name in sorted(set(volumes) & present_volumes):
        if run(['docker', 'volume', 'rm', name], check=False).returncode:
            failures.append(name)
    present_containers, present_volumes = resources(run)
    remaining = (set(containers) & present_containers) | (set(volumes) & present_volumes)
    if failures or remaining:
        raise RuntimeError('proof cleanup failed: ' + ', '.join(sorted(set(failures) | remaining)))
