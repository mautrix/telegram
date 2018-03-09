"""Add metadata to TelegramFile

Revision ID: cfc972368e50
Revises: 501dad2868bc
Create Date: 2018-03-09 16:07:01.236712

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'cfc972368e50'
down_revision = '501dad2868bc'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("telegram_file") as batch_op:
        batch_op.add_column(sa.Column('size', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('width', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('height', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('thumbnail', sa.String(), nullable=True))
        batch_op.create_foreign_key(constraint_name="fk_file_thumbnail",
                                    referent_table="telegram_file",
                                    local_cols=['thumbnail'],
                                    remote_cols=['id'])


def downgrade():
    with op.batch_alter_table("telegram_file") as batch_op:
        batch_op.drop_column('size')
        batch_op.drop_column('width')
        batch_op.drop_column('height')
        batch_op.drop_column('thumbnail')
