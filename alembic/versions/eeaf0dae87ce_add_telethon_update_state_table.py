"""Add telethon update state table

Revision ID: eeaf0dae87ce
Revises: 1fa46383a9d3
Create Date: 2018-04-30 17:30:59.610885

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'eeaf0dae87ce'
down_revision = '1fa46383a9d3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("telethon_entities") as batch_op:
        batch_op.alter_column('phone', existing_type=sa.Integer, type_=sa.BigInteger)
    op.create_table('telethon_update_state',
                    sa.Column('session_id', sa.String, nullable=False),
                    sa.Column('entity_id', sa.Integer, nullable=False),
                    sa.Column('pts', sa.Integer, nullable=True),
                    sa.Column('qts', sa.Integer, nullable=True),
                    sa.Column('date', sa.Integer, nullable=True),
                    sa.Column('seq', sa.Integer, nullable=True),
                    sa.PrimaryKeyConstraint('session_id', 'entity_id'))


def downgrade():
    with op.batch_alter_table("telethon_entities") as batch_op:
        batch_op.alter_column('phone', existing_type=sa.BigInteger, type_=sa.Integer)
    op.drop_table('telethon_update_state')
