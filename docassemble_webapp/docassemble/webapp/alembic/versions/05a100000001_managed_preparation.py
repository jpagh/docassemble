"""Add managed preparation records without adopting any legacy intent.

Revision ID: 05a100000001
Revises: fd1547c94c46
"""
from alembic import op
from sqlalchemy.schema import CreateTable
from sqlalchemy import CheckConstraint
from docassemble.webapp.database import dbtableprefix
from docassemble.base.managed_records import preparation_tables, readiness_guard_sql

revision = '05a100000001'
down_revision = 'fd1547c94c46'
branch_labels = None
depends_on = None


def upgrade():
    tables = preparation_tables(dbtableprefix)
    # Freeze the original 05a1 state domain despite the shared factory evolving.
    candidate = tables['candidate']
    # 05b5 adds this column; the live factory must not rewrite this revision.
    candidate._columns.remove(candidate.c.credential_request_binding)
    for constraint in list(candidate.constraints):
        if isinstance(constraint, CheckConstraint) and 'state IN' in str(constraint.sqltext):
            candidate.constraints.remove(constraint)
    candidate.append_constraint(CheckConstraint("state IN ('queued','preparing','ready','cancelled','expired','failed')"))
    metadata = next(iter(tables.values())).metadata
    for table in metadata.sorted_tables:
        if table not in (tables['publication_result'], tables['retry_result'], tables['account_cleanup']):
            op.execute(CreateTable(table))
    if op.get_bind().dialect.name == 'postgresql':
        for statement in readiness_guard_sql(dbtableprefix):
            op.execute(statement.replace("'published', ", ''))


def downgrade():
    tables = preparation_tables(dbtableprefix)
    for table in reversed(next(iter(tables.values())).metadata.sorted_tables):
        if table not in (tables['publication_result'], tables['retry_result'], tables['account_cleanup']):
            op.drop_table(table.name)
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION "' + dbtableprefix + 'managed_ready_immutable"()')
