"""Add portal-specific config

Revision ID: b54929c22c86
Revises: d5f7b8b4b456
Create Date: 2018-09-24 23:40:33.528710

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b54929c22c86"
down_revision = "d5f7b8b4b456"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("portal", sa.Column("config", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("portal") as batch_op:
        batch_op.drop_column("config")
