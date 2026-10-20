"""Additive preparation records. No legacy intent is inferred or seeded.

This module has no Flask imports; the independent preparation protocol consumes
these same SQLAlchemy table definitions. Installation records are storage only.
"""
from sqlalchemy import (MetaData, Table, Column, Integer, BigInteger, Text, String, Boolean,
                        DateTime, JSON, ForeignKey, UniqueConstraint, CheckConstraint, DDL, event)


def preparation_tables(prefix='', metadata=None):
    metadata = metadata if metadata is not None else MetaData()
    def table(name, *columns):
        return Table(prefix + 'managed_' + name, metadata, *columns)
    def ref(name):
        return ForeignKey(prefix + 'managed_' + name + '.id')
    tables = {}
    tables['revision'] = table('revision', Column('id', String(255), primary_key=True),
        Column('parent_id', String(255), ref('revision')), Column('snapshot', JSON, nullable=False))
    tables['installation_attempt'] = table('installation_attempt', Column('id', String(255), primary_key=True),
        Column('revision_id', String(255), ref('revision'), nullable=False),
        Column('record', JSON, nullable=False))
    tables['environment_state'] = table('environment_state', Column('id', Integer, primary_key=True),
        Column('managed', Boolean, nullable=False), Column('desired_revision_id', String(255), ref('revision')),
        Column('active_attempt_id', String(255), ref('installation_attempt')),
        Column('publication_generation', BigInteger, nullable=False),
        Column('input_generation', BigInteger, nullable=False),
        Column('target_generation', BigInteger, nullable=False),
        Column('runtime_generation', BigInteger, nullable=False),
        Column('snapshot', JSON, nullable=False), CheckConstraint('id = 1'))
    tables['package_identity'] = table('package_identity', Column('id', String(255), primary_key=True),
        Column('normalized_name', String(255), nullable=False, unique=True),
        Column('display_name', Text, nullable=False), Column('legacy_package_id', Integer),
        Column('owner_id', Text))
    tables['requirement_contribution'] = table('requirement_contribution',
        Column('id', String(255), primary_key=True), Column('identity_id', String(255), ref('package_identity'), nullable=False),
        Column('origin', Text, nullable=False), Column('declaration', Text, nullable=False),
        Column('active', Boolean, nullable=False), Column('artifact_ref', Text))
    tables['host_environment'] = table('host_environment', Column('id', String(255), primary_key=True),
        Column('generation', BigInteger, nullable=False), Column('observation', JSON, nullable=False))
    tables['observed_config'] = table('observed_config', Column('id', String(255), primary_key=True),
        Column('generation', BigInteger, nullable=False), Column('snapshot', JSON, nullable=False))
    tables['candidate'] = table('candidate', Column('id', String(255), primary_key=True),
        Column('requester_id', String(255), nullable=False), Column('operation', String(255), nullable=False),
        Column('request_digest', Text, nullable=False), Column('parent_id', String(255), ref('revision')),
        Column('publication_generation', BigInteger, nullable=False),
        Column('input_generation', BigInteger, nullable=False),
        Column('target_generation', BigInteger, nullable=False),
        Column('runtime_generation', BigInteger, nullable=False),
        Column('inputs', JSON, nullable=False), Column('credential_request_binding', JSON, nullable=True),
        Column('state', Text, nullable=False),
        Column('fence', BigInteger, nullable=False), Column('claim_until', DateTime(timezone=True)),
        Column('result', JSON), Column('result_digest', Text),
        Column('ready_at', DateTime(timezone=True)), Column('expires_at', DateTime(timezone=True)),
        CheckConstraint("state IN ('queued','preparing','ready','published','cancelled','expired','failed')"),
        CheckConstraint("(result IS NULL AND result_digest IS NULL AND ready_at IS NULL AND expires_at IS NULL) OR "
                        "(result IS NOT NULL AND result_digest IS NOT NULL AND ready_at IS NOT NULL AND expires_at IS NOT NULL)"))
    tables['request_deduplication'] = table('request_deduplication',
        Column('actor_id', String(255), primary_key=True), Column('operation', String(255), primary_key=True),
        Column('key', String(255), primary_key=True), Column('request_digest', Text, nullable=False),
        Column('candidate_id', String(255), ref('candidate'), nullable=False))
    tables['publication_result'] = table('publication_result',
        Column('actor_id', String(255), primary_key=True),
        Column('key', String(255), primary_key=True),
        Column('request_digest', Text, nullable=False),
        Column('candidate_id', String(255), ref('candidate'), nullable=False, unique=True),
        Column('revision_id', String(255), ref('revision'), nullable=False, unique=True),
        Column('attempt_id', String(255), ref('installation_attempt'), nullable=False, unique=True))
    tables['retry_result'] = table('retry_result',
        Column('actor_id', String(255), primary_key=True),
        Column('key', String(255), primary_key=True),
        Column('request_digest', Text, nullable=False),
        Column('revision_id', String(255), ref('revision'), nullable=False),
        Column('attempt_id', String(255), ref('installation_attempt'), nullable=False, unique=True))
    tables['account_cleanup'] = table('account_cleanup',
        Column('user_id', Integer, primary_key=True), Column('state', Text, nullable=False),
        Column('error', Text), CheckConstraint("state IN ('pending','complete','failed')"))
    tables['preparation_worker'] = table('preparation_worker', Column('id', String(255), primary_key=True),
        Column('candidate_id', String(255), ref('candidate'), nullable=False), Column('fence', BigInteger, nullable=False),
        Column('exited', Boolean, nullable=False),
        UniqueConstraint('candidate_id', 'fence'))
    tables['candidate_material'] = table('candidate_material', Column('id', String(255), primary_key=True),
        Column('candidate_id', String(255), ref('candidate')), Column('path', Text, nullable=False),
        Column('usable', Boolean, nullable=False), Column('committed', Boolean, nullable=False),
        Column('shared', Boolean, nullable=False), Column('deleted', Boolean, nullable=False))
    # Table creation events cover fresh metadata.create_all initialization.
    # The migration executes CreateTable directly (without these events) and
    # installs the same SQL explicitly after all tables have been created.
    guards = readiness_guard_sql(prefix)
    for name, statements in (('revision', (guards[0], guards[2])),
                             ('candidate', (guards[1],))):
        for statement in statements:
            event.listen(tables[name], 'after_create', DDL(statement).execute_if(dialect='postgresql'))
    return tables


def readiness_guard_sql(prefix=''):
    """PostgreSQL guards prevent accidental rewriting of an attested ready result.

    Prefix comes from trusted installation configuration, not request input.
    Cancel/expiry may change authority state, never historical result bytes.
    """
    if not all(char.isalnum() or char == '_' for char in prefix):
        raise ValueError('invalid database table prefix')
    function = '"' + prefix + 'managed_ready_immutable"'
    candidate = '"' + prefix + 'managed_candidate"'
    revision = '"' + prefix + 'managed_revision"'
    return [
        f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_TABLE_NAME = '{prefix}managed_revision' THEN
            RAISE EXCEPTION 'revision is immutable';
          END IF;
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'candidate history is retained';
          END IF;
          IF OLD.state IN ('published', 'cancelled', 'expired', 'failed') AND NEW.state <> OLD.state THEN
            RAISE EXCEPTION 'terminal candidate cannot be resurrected';
          END IF;
          IF OLD.result_digest IS NULL AND NEW.result_digest IS NOT NULL AND (
            OLD.state <> 'preparing' OR NEW.state <> 'ready' OR
            NEW.expires_at - NEW.ready_at <> interval '5 minutes'
          ) THEN
            RAISE EXCEPTION 'invalid readiness transition';
          END IF;
          IF OLD.result_digest IS NOT NULL AND (
            NEW.id IS DISTINCT FROM OLD.id OR
            NEW.requester_id IS DISTINCT FROM OLD.requester_id OR
            NEW.operation IS DISTINCT FROM OLD.operation OR
            NEW.request_digest IS DISTINCT FROM OLD.request_digest OR
            NEW.result::jsonb IS DISTINCT FROM OLD.result::jsonb OR
            NEW.result_digest IS DISTINCT FROM OLD.result_digest OR
            NEW.ready_at IS DISTINCT FROM OLD.ready_at OR
            NEW.expires_at IS DISTINCT FROM OLD.expires_at OR
            NEW.inputs::jsonb IS DISTINCT FROM OLD.inputs::jsonb OR
            NEW.parent_id IS DISTINCT FROM OLD.parent_id OR
            NEW.publication_generation IS DISTINCT FROM OLD.publication_generation OR
            NEW.input_generation IS DISTINCT FROM OLD.input_generation OR
            NEW.target_generation IS DISTINCT FROM OLD.target_generation OR
            NEW.runtime_generation IS DISTINCT FROM OLD.runtime_generation
          ) THEN
            RAISE EXCEPTION 'ready result is immutable';
          END IF;
          RETURN NEW;
        END $$''',
        f'CREATE TRIGGER managed_ready_immutable BEFORE UPDATE OR DELETE ON {candidate} '
        f'FOR EACH ROW EXECUTE FUNCTION {function}()',
        f'CREATE TRIGGER managed_revision_immutable BEFORE UPDATE OR DELETE ON {revision} '
        f'FOR EACH ROW EXECUTE FUNCTION {function}()',
    ]
