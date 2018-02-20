"""Add TelegramFile table

Revision ID: 1b241f7e8530
Revises: 97d2a942bcf8
Create Date: 2018-02-19 23:52:06.605741

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '1b241f7e8530'
down_revision = '97d2a942bcf8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('telegram_file',
                    sa.Column('id', sa.String(), nullable=False),
                    sa.Column('mxc', sa.String(), nullable=True),
                    sa.Column('mime_type', sa.String(), nullable=True),
                    sa.Column('was_converted', sa.Boolean(), nullable=True),
                    sa.PrimaryKeyConstraint('id'))


def downgrade():
    op.drop_table('telegram_file')
