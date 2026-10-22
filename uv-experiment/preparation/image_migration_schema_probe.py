"""Read-only replacement-image and live schema checks before migration proof work."""
from docassemble.base.managed_python import python_site_packages
import hashlib
import json
from pathlib import Path
import sys
import zipfile


from docassemble_executor.paths import ExecutorPaths

RUNTIME = ExecutorPaths.from_env().runtime_venv
BUNDLES = Path('/usr/share/docassemble/managed/bundles')


def check_image_schema(expected: dict[str, str], runtime: Path = RUNTIME,
                       bundles: Path = BUNDLES) -> dict[str, dict[str, str]]:
    """Inspect shipped bytes, not repository modules shadowing an outdated image."""
    evidence = {}
    installed = runtime / python_site_packages()
    evidence['runtime'] = {}
    for member, digest in expected.items():
        actual = hashlib.sha256((installed / member).read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError('runtime/' + member + ' differs from the current checkout')
        evidence['runtime'][member] = actual
    for bundle in ('core-r1', 'runtime-r1'):
        wheels = bundles / bundle / 'wheels'
        evidence[bundle] = {}
        for project in ('base', 'webapp'):
            candidates = list(wheels.glob('docassemble_' + project + '-*.whl'))
            if len(candidates) != 1:
                raise ValueError(bundle + ' must contain exactly one docassemble.' + project + ' wheel')
            with zipfile.ZipFile(candidates[0]) as archive:
                for member, digest in expected.items():
                    if not member.startswith('docassemble/' + project + '/'):
                        continue
                    actual = hashlib.sha256(archive.read(member)).hexdigest()
                    if actual != digest:
                        raise ValueError(bundle + '/' + member + ' differs from the current checkout')
                    evidence[bundle][member] = actual
    return evidence


def check_database_schema() -> dict[str, str]:
    from sqlalchemy import inspect
    from sqlalchemy.exc import NoSuchTableError
    from image_bridge_proof import database

    engine, prefix = database()
    table = prefix + 'managed_candidate'
    try:
        try:
            columns = {column['name'] for column in inspect(engine).get_columns(table)}
        except NoSuchTableError:
            columns = set()
        if 'credential_request_binding' not in columns:
            raise ValueError(table + '.credential_request_binding is missing after image startup; '
                             'Alembic migration 05b500000001 has not been applied')
        return {'table': table, 'column': 'credential_request_binding'}
    finally:
        engine.dispose()


def main() -> int:
    try:
        if sys.argv[1] == 'image':
            result = check_image_schema(json.loads(sys.argv[2]))
        elif sys.argv[1] == 'database':
            result = check_database_schema()
        else:
            raise ValueError('unknown schema probe action')
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print('image migration schema preflight: ' + str(error), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
