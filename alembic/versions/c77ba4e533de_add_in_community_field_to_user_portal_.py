"""Add in_community field to user_portal and contact

Revision ID: c77ba4e533de
Revises: 888275d58e57
Create Date: 2020-10-27 13:14:18.332226

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c77ba4e533de'
down_revision = '888275d58e57'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('contact') as batch_op:
        batch_op.add_column(sa.Column('in_community', sa.Boolean(), nullable=False))

    with op.batch_alter_table('user_portal') as batch_op:
        batch_op.add_column(sa.Column('in_community', sa.Boolean(), nullable=False))


def downgrade():
    with op.batch_alter_table('user_portal') as batch_op:
        batch_op.drop_column('in_community')

    with op.batch_alter_table('contact') as batch_op:
        batch_op.drop_column('in_community')
