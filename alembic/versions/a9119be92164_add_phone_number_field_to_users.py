"""Add phone number field to users

Revision ID: a9119be92164
Revises: b54929c22c86
Create Date: 2018-09-28 02:38:40.626282

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a9119be92164"
down_revision = "b54929c22c86"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("tg_phone", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("tg_phone")
