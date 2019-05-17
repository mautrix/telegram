"""Add disable_updates field for puppets

Revision ID: 17574c57f3f8
Revises: a9119be92164
Create Date: 2019-05-15 00:24:46.967529

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '17574c57f3f8'
down_revision = 'a9119be92164'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.add_column(sa.Column("disable_updates", sa.Boolean(), nullable=False,
                                      server_default=sa.sql.expression.false()))


def downgrade():
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.drop_column("disable_updates")
