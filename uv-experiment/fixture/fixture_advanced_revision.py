"""Proof-only additive migration; deliberately absent from retained core.

The recovery compatibility inventory explicitly qualifies this revision. It
adds an unrelated table and changes no core columns or constraints.
"""

from alembic import op
import sqlalchemy as sa

revision = 'fixture_advanced_revision'
# Must follow the current production head; branching from the same parent would
# create two Alembic heads, which fresh initialization now refuses.
down_revision = '05b500000001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('fixture_advanced_evidence', sa.Column('id', sa.Integer(), primary_key=True))


def downgrade():
    raise RuntimeError('The proof never rolls back its database')
