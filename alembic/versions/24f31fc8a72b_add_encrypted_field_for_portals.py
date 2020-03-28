"""Add encrypted field for portals

Revision ID: 24f31fc8a72b
Revises: a7c04a56041b
Create Date: 2020-03-28 20:14:29.046699

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "24f31fc8a72b"
down_revision = "a7c04a56041b"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("portal") as batch_op:
        batch_op.add_column(sa.Column("encrypted", sa.Boolean(), nullable=False,
                                      server_default=sa.sql.expression.false()))


def downgrade():
    with op.batch_alter_table("portal") as batch_op:
        batch_op.drop_column("encrypted")
