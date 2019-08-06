"""Store custom puppet next_batch in database

Revision ID: a7c04a56041b
Revises: 4f7d7ed5792a
Create Date: 2019-08-06 23:08:51.087651

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a7c04a56041b"
down_revision = "4f7d7ed5792a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.add_column(sa.Column("next_batch", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.drop_column("next_batch")
