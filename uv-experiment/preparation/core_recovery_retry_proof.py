"""Controlled runtime-side real administrator authorization; no HTTP admission needed."""
import json
import os
import sys

from sqlalchemy import select


def retry_state(service):
    """Capture immutable retry intent and the singleton/attempt authority."""
    with service.engine.connect() as connection:
        return {name: sorted((dict(row) for row in connection.execute(
                    select(service.tables[name])).mappings()), key=lambda row: row['id'])
                for name in ('environment_state', 'revision', 'package_identity',
                             'requirement_contribution', 'installation_attempt')}


def assert_exact_revision_retry(service, actor, revision, idempotency_key):
    before = retry_state(service)
    assert before['environment_state'][0]['desired_revision_id'] == revision
    assert before['environment_state'][0]['active_attempt_id'] is None
    retry = service.retry(actor, revision, idempotency_key)
    assert retry['revision_id'] == revision
    after = retry_state(service)
    for name in ('revision', 'package_identity', 'requirement_contribution'):
        assert after[name] == before[name]
    attempt_id = retry['attempt_id']
    assert after['environment_state'] == [dict(before['environment_state'][0], active_attempt_id=attempt_id)]
    assert len(after['installation_attempt']) == len(before['installation_attempt']) + 1
    assert [row for row in after['installation_attempt'] if row['id'] != attempt_id] == before['installation_attempt']
    pending = next(row for row in after['installation_attempt'] if row['id'] == attempt_id)
    assert pending['revision_id'] == revision
    assert pending['record']['phase'] == 'pending' and pending['record']['reason'] == 'retry'
    assert service.retry(actor, revision, idempotency_key) == retry
    assert retry_state(service) == after
    return retry


def main():
    from sqlalchemy import create_engine
    from docassemble.base.managed_qualification import configured_baseline
    from docassemble.webapp.packages.managed import QualifiedPublication, PreviewAuthorizer

    engine = create_engine(os.environ['DA_PREPARATION_DATABASE_URL'])
    prefix, revision = sys.argv[1:]
    policy = {'allowed': {}, 'allow_updates': True, 'developer_can_install': True}
    authorizer = PreviewAuthorizer(prefix, lambda: policy)
    service = QualifiedPublication(engine, authorizer, lambda *_: {}, prefix, configured_baseline())
    retry = assert_exact_revision_retry(service, '1', revision, 'recovered-exact-retry')
    print('PASS real administrator-authorized exact-revision retry; immutable intent/prior terminal preserved; one pending attempt/slot')
    print('PROOF_RETRY=' + json.dumps(retry, sort_keys=True))


if __name__ == '__main__':
    main()
