"""Add Matrix redaction state to message table

Revision ID: 7de69cf5809e
Revises: 888275d58e57
Create Date: 2020-12-19 12:39:57.368568

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '7de69cf5809e'
down_revision = '888275d58e57'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('message', schema=None) as batch_op:
        batch_op.add_column(sa.Column('redacted', sa.Boolean(), server_default=sa.false(), nullable=True))


def downgrade():
    with op.batch_alter_table('message', schema=None) as batch_op:
        batch_op.drop_column('redacted')
