"""Add decryption info field for reuploaded telegram files

Revision ID: d3c922a6acd2
Revises: 24f31fc8a72b
Create Date: 2020-03-30 20:07:17.340346

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3c922a6acd2'
down_revision = '24f31fc8a72b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("telegram_file") as batch_op:
        batch_op.add_column(sa.Column("decryption_info", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("telegram_file") as batch_op:
        batch_op.drop_column("decryption_info")
