"""increase account activity column sizes

Revision ID: 143181919790
Revises: 97404229e75e
Create Date: 2021-10-08 11:21:27.519129

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '143181919790'
down_revision = '97404229e75e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_activity', schema=None) as batch_op:
        batch_op.alter_column('first_activity_ts',
               existing_type=sa.INTEGER(),
               type_=sa.BigInteger(),
               existing_nullable=True)
        batch_op.alter_column('last_activity_ts',
               existing_type=sa.INTEGER(),
               type_=sa.BigInteger(),
               existing_nullable=True)


def downgrade():
    with op.batch_alter_table('user_activity', schema=None) as batch_op:
        batch_op.alter_column('last_activity_ts',
               existing_type=sa.BigInteger(),
               type_=sa.INTEGER(),
               existing_nullable=True)
        batch_op.alter_column('first_activity_ts',
               existing_type=sa.BigInteger(),
               type_=sa.INTEGER(),
               existing_nullable=True)
