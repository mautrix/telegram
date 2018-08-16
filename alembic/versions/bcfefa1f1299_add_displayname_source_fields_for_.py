"""Add displayname source fields for puppets

Revision ID: bcfefa1f1299
Revises: bdadd173ee02
Create Date: 2018-05-19 17:00:21.078098

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'bcfefa1f1299'
down_revision = 'bdadd173ee02'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('puppet', sa.Column('displayname_source', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.drop_column('displayname_source')
