"""Durable fail-closed account cleanup, independent of non-DB stores."""
from alembic import op
from sqlalchemy.schema import CreateTable
from docassemble.webapp.database import dbtableprefix
from docassemble.base.managed_records import preparation_tables

revision = '05b400000001'
down_revision = '05b300000001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(CreateTable(preparation_tables(dbtableprefix)['account_cleanup']))


def downgrade():
    raise RuntimeError('account cleanup downgrade requires explicit archival migration')
