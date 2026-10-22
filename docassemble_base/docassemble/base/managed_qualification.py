"""Explicit experimental image-baseline gate, not adoption or core upgrade authority."""
from docassemble.base.managed_python import python_target
import json
import os
from pathlib import Path

from docassemble.base.managed_config import private_source_policy_config
from docassemble.base.managed_preparation import PreparationError

ENVIRONMENT = 'DA_QUALIFIED_IMAGE_BASELINE'
PUBLIC_CONFIG = {'index_url': 'https://pypi.org/simple'}


def _qualified_config(config) -> bool:
    """Public PyPI or an operator-provisioned credential-free private policy."""
    return config == PUBLIC_CONFIG or private_source_policy_config(config)


def configured_baseline():
    path = os.environ.get(ENVIRONMENT)
    if not path or os.environ.get('CONTAINERROLE', 'all').strip(':') != 'all':
        raise PreparationError('qualified all-role image baseline required')
    try:
        document = json.loads(Path(path).read_text())
        baseline = document['baseline']
        if (document['format'] != 1 or baseline['target'] != python_target() or
                baseline['tool'] != {'uv': '0.12.21'} or
                not _qualified_config(baseline['config']) or
                not baseline['core'] or not baseline['image_baseline']):
            raise ValueError()
        return baseline
    except (OSError, ValueError, KeyError, TypeError):
        raise PreparationError('invalid qualified image baseline') from None


def _effective_config(inputs):
    """The source policy a candidate would commit, including a pending config import."""
    config = inputs['baseline'].get('config')
    if inputs.get('change_operation') == 'rollback':
        return inputs['rollback']['snapshot']['config']
    if inputs.get('change_operation') == 'config':
        from docassemble.base.managed_config import config_import_policy
        config = config_import_policy(inputs['config_import']['snapshot'], config)
    return config


def require_qualified_inputs(inputs, baseline):
    captured = inputs['baseline']
    if any(captured.get(key) != baseline.get(key) for key in ('target', 'tool', 'image_baseline')):
        raise PreparationError('unsupported image baseline')
    selected = captured.get('selected_baseline')
    if selected:
        try:
            bundle, release = selected['core_bundle'], selected['release']
            if (captured['core'] != bundle['inventory'] or release['core_bundle'] != bundle['digest'] or
                    any(bundle['inventory']['docassemble-' + name]['version'] != release[name]
                        for name in ('base', 'webapp', 'demo')) or
                    release['base'] != release['webapp'] or release['demo'] != release['webapp']):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise PreparationError('unsupported selected baseline') from None
    elif captured.get('core') != baseline.get('core'):
        raise PreparationError('unsupported image baseline')
    # Compare the effective source policy, not raw config equality: a committed
    # private policy (or a pending config import) must match the operator-provisioned
    # baseline, and any divergence stays fail-closed.
    if _effective_config(inputs) != baseline.get('config'):
        raise PreparationError('unsupported image baseline')
    # Assignment is explicit and current-admin-authorized by the SQL consumers;
    # adoption itself must never invent an owner. Exact image core stays fenced.
    if (inputs.get('change_operation') not in ('config', 'repair', 'upgrade', 'rollback', 'adoption', 'migration') and
            inputs['requirement']['normalized_name'] in captured['core']):
        raise PreparationError('image core changes unsupported')


def bridge_http_path(path, *, recovery=False):
    """Qualified application ingress, not uncoordinated account/grant writers.

    Forwarded HTTP retains admission/caller leases and file authorization. Socket
    transports retain the service's Engine.IO lifetime scopes, not relay leases.
    W separately restricts recovery to authentication, config, logs and managed
    repair/retry, never interviews/files/sockets. Config saves use the
    unresolved-save publication fence.
    """
    if recovery:
        return (path in ('/health_check', '/health_status', '/status.json', '/user/sign-in',
                        '/user/sign-out', '/user/post-sign-in', '/config', '/logs',
                        '/recovery', '/recovery/status.json', '/recovery/publication', '/recovery/repair',
                        '/bundle.css', '/bundle.js', '/adminbundle.js',
                        '/bundlewrapjquery.js', '/bundlenojquery.js') or
                path.startswith(('/static/', '/logfile/', '/rjs/', '/packagestatic/')))
    return (path in ('/health_check', '/health_status', '/status.json', '/user/sign-in',
                     '/user/sign-out', '/user/post-sign-in', '/package_preview', '/api/package_preview',
                     '/api/package', '/api/package_update_status', '/updatepackage',
                     '/createplaygroundpackage', '/config', '/logs', '/', '/interview', '/i',
                     '/interviews', '/list', '/checkin', '/checkout', '/leave', '/restart_session',
                     '/new_session', '/exit', '/exit_logout', '/after_reset', '/launch',
                     '/resume', '/goto', '/socket.io', '/socket.io/',
                     '/bundle.css', '/bundle.js', '/adminbundle.js',
                     '/bundlewrapjquery.js', '/bundlenojquery.js',
                     '/recovery', '/recovery/status.json', '/recovery/publication', '/recovery/repair') or
            path.startswith(('/static/', '/logfile/', '/start/', '/run/', '/rjs/', '/packagestatic/',
                             '/storedfile/', '/storedfiledownload/', '/tempfile/', '/tempfiledownload/',
                             '/uploadedfile/', '/uploadedfiledownload/', '/uploadedpage/',
                             '/uploadedpagedownload/', '/uploadedpagescreen/', '/uploadedpagescreendownload/')))


def installation_retry_eligible(receipt: dict) -> bool:
    """Terminal evidence only; callers still enforce durable equality and authority.

    Eligibility never clears uncertain schema evidence or authorizes mutation.
    """
    if not isinstance(receipt, dict) or receipt.get('normal_verified') is not False:
        return False
    if (receipt.get('outcome') == 'aborted-before-sync' and
            receipt.get('mutation_started') is False and receipt.get('child_safe') is False and
            receipt.get('failure') == {'stage': 'drain', 'reason': 'drain-aborted'}):
        return True
    recovery = receipt.get('recovery')
    identity = receipt.get('identity')
    return bool(receipt.get('outcome') == 'failed-stopped' and
        receipt.get('mutation_started') is True and receipt.get('child_safe') is True and
        isinstance(identity, dict) and isinstance(identity.get('attempt_id'), str) and identity['attempt_id'] and
        isinstance(recovery, dict) and recovery.get('result') == 'succeeded' and
        type(recovery.get('attempt')) is int and recovery['attempt'] == 1 and
        recovery.get('id') == identity['attempt_id'] + ':core:1' and
        isinstance(recovery.get('core_baseline'), dict) and recovery['core_baseline'] and
        isinstance(recovery.get('inventory'), dict) and recovery['inventory'])
