"""Private per-candidate credential equality, outside public readiness inputs."""
from alembic import op
import sqlalchemy as sa
from docassemble.webapp.database import dbtableprefix

revision = '05b500000001'
down_revision = '05b400000001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(dbtableprefix + 'managed_candidate',
                  sa.Column('credential_request_binding', sa.JSON(), nullable=True))


def downgrade():
    raise RuntimeError('credential equality downgrade requires explicit archival migration')
