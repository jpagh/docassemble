"""Retained, bundle-bound negative schema knowledge; never schema certification.

The wheel's repeatable Docassemble-Incompatible-Schema-Revision metadata header
is release-authored negative knowledge. Unknown revisions remain unproven, not
known incompatible. Runtime/web/role probes remain mandatory and limited.
"""
import ast
from email.parser import BytesParser
import re
import zipfile

from .alembic_probe import SchemaCheck, STATUS_UNPROVEN, STATUS_INCOMPATIBLE, STATUS_UNREADABLE, check_schema
from .preparation import PreparationError


def retained_schema_policy(directory):
    from packaging.utils import canonicalize_name, parse_wheel_filename
    wheels = [path for path in (directory / 'wheels').glob('*.whl')
              if canonicalize_name(parse_wheel_filename(path.name)[0]) == 'docassemble-webapp']
    if len(wheels) != 1:
        raise PreparationError('retained schema inventory unavailable')
    with zipfile.ZipFile(wheels[0]) as wheel:
        metadata = [name for name in wheel.namelist() if name.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise PreparationError('retained schema policy unavailable')
        headers = BytesParser().parsebytes(wheel.read(metadata[0]))
        incompatible = headers.get_all('Docassemble-Incompatible-Schema-Revision', [])
        if any(not re.fullmatch('[a-zA-Z0-9_-]+', value) for value in incompatible):
            raise PreparationError('invalid retained schema policy')
        revisions = set()
        for name in wheel.namelist():
            if '/alembic/versions/' not in name or not name.endswith('.py'):
                continue
            tree = ast.parse(wheel.read(name))
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'revision'
                                                       for target in node.targets):
                    try:
                        value = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(value, str) and value:
                        revisions.add(value)
        if not revisions:
            raise PreparationError('retained schema inventory unreadable')
    return {'source': 'retained-webapp-wheel-metadata', 'known_incompatible_revisions': sorted(set(incompatible)),
            'known_revisions': sorted(revisions)}


def check_rollback_schema(venv, settings, directory):
    policy = retained_schema_policy(directory)
    observed = check_schema(venv, settings)
    if observed.status == STATUS_UNREADABLE or observed.database_revision is None:
        return observed
    if observed.database_revision in policy['known_incompatible_revisions']:
        return SchemaCheck(STATUS_INCOMPATIBLE, 'release explicitly records this database revision as incompatible',
                           observed.database_revision, observed.known_revisions)
    if observed.database_revision in policy['known_revisions']:
        return observed
    # usable permits bounded nonmutating qualification, not a claim of compatibility.
    return SchemaCheck(STATUS_UNPROVEN, 'unproven advanced/unknown schema; nonmutating checks cannot prove data compatibility',
                       observed.database_revision, observed.known_revisions)
