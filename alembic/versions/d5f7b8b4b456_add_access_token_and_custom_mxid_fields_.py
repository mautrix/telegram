"""Add access_token and custom_mxid fields for puppets

Revision ID: d5f7b8b4b456
Revises: 6ca3d74d51e4
Create Date: 2018-07-20 12:09:30.277960

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d5f7b8b4b456"
down_revision = "6ca3d74d51e4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("puppet", sa.Column("access_token", sa.String(), nullable=True))
    op.add_column("puppet", sa.Column("custom_mxid", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.drop_column("custom_mxid")
        batch_op.drop_column("access_token")
