"""Internal publication records; public apply-producing approval remains fenced."""
from alembic import op
from sqlalchemy.schema import CreateTable
from docassemble.webapp.database import dbtableprefix
from docassemble.base.managed_records import preparation_tables, readiness_guard_sql

revision = '05b100000001'
down_revision = '05a100000001'
branch_labels = None
depends_on = None


def upgrade():
    tables = preparation_tables(dbtableprefix)
    op.execute(CreateTable(tables['publication_result']))
    if op.get_bind().dialect.name == 'postgresql':
        candidate = '"' + dbtableprefix + 'managed_candidate"'
        # The original check was unnamed; PostgreSQL chooses its generated name.
        # Locate by definition instead of assuming an installation's constraint name.
        op.execute(f'''DO $$ DECLARE constraint_name text; BEGIN
          FOR constraint_name IN SELECT conname FROM pg_constraint
            WHERE conrelid = '{candidate}'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%state%queued%'
          LOOP EXECUTE format('ALTER TABLE {candidate} DROP CONSTRAINT %I', constraint_name); END LOOP;
        END $$''')
        op.execute(f'''ALTER TABLE {candidate} ADD CONSTRAINT managed_candidate_state
            CHECK (state IN ('queued','preparing','ready','published','cancelled','expired','failed'))''')
        function = readiness_guard_sql(dbtableprefix)[0].replace('CREATE FUNCTION', 'CREATE OR REPLACE FUNCTION', 1)
        op.execute(function)


def downgrade():
    # Published history cannot safely be downgraded into preview-only semantics.
    raise RuntimeError('managed publication downgrade requires explicit archival migration')
