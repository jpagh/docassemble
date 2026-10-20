"""Internal retry deduplication; public apply-producing operations stay fenced."""
from alembic import op
from sqlalchemy.schema import CreateTable
from docassemble.webapp.database import dbtableprefix
from docassemble.base.managed_records import preparation_tables

revision = '05b300000001'
down_revision = '05b100000001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(CreateTable(preparation_tables(dbtableprefix)['retry_result']))


def downgrade():
    raise RuntimeError('managed retry downgrade requires explicit archival migration')
