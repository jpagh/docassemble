"""Exact retained-revision rollback disclosure and immutable-result contract."""
from docassemble.base.managed_preparation import Preparation, PreparationError


def disclosure(inputs):
    historical = inputs['rollback']['snapshot']
    return {
        'revision_id': inputs['rollback']['revision_id'],
        'requirements': {
            'before': [row['declaration'] for row in inputs['committed_contributions']],
            'after': [row['declaration'] for row in inputs['contributions']]},
        'core': {'before': inputs['baseline']['core'], 'after': historical['core']},
        'database_unchanged': True,
        'risks': ['package-rollback-does-not-restore-data',
                  'unknown-schema-revision-is-unproven-not-known-incompatible',
                  'nonmutating-checks-do-not-prove-full-data-compatibility'],
    }


def result_for(inputs):
    historical = inputs['rollback']['snapshot']
    bundle = historical['publication']['result']['bundle']
    inventory = bundle['inventory']

    def diff(before):
        return {name: {'before': before.get(name), 'after': inventory.get(name)}
                for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}

    baseline = inputs['baseline']
    result = {'bundle': bundle, 'rollback': disclosure(inputs),
              'desired_diff': diff(baseline['desired_inventory']),
              'runtime_diff': diff(baseline['runtime']['inventory']) if baseline['runtime']['kind'] == 'normal' else None,
              'warnings': Preparation.ready_warnings(inputs),
              'validation': {'exact_retained_solution': True, 'no_solve': True,
                             'durable_materialization_verified': True},
              'ownership_effects': inputs['ownership_effects']}
    for key in ('git_sources', 'direct_sources'):
        if key in historical or key in baseline:
            result[key] = historical.get(key, {})
    return result


def validate_result(inputs, result):
    if result != result_for(inputs):
        raise PreparationError('incomplete or invalid exact rollback result')
