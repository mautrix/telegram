"""Add megagroup field to portals

Revision ID: 30eca60587f1
Revises: cfc972368e50
Create Date: 2018-04-29 15:51:04.656605

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '30eca60587f1'
down_revision = 'cfc972368e50'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("portal") as batch_op:
        batch_op.add_column(sa.Column('megagroup', sa.Boolean()))


def downgrade():
    with op.batch_alter_table("portal") as batch_op:
        batch_op.drop_column('megagroup')
