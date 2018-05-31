"""Add cascade rules to UserPortal

Revision ID: 2228d49c383f
Revises: bcfefa1f1299
Create Date: 2018-05-31 11:11:59.482112

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2228d49c383f'
down_revision = 'bcfefa1f1299'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('user_portal_user_fkey', 'user_portal', type_='foreignkey')
    op.drop_constraint('user_portal_portal_fkey', 'user_portal', type_='foreignkey')
    op.create_foreign_key('user_portal_user_fkey', 'user_portal', 'user', ['user'], ['tgid'], onupdate='CASCADE', ondelete='CASCADE')
    op.create_foreign_key('user_portal_portal_fkey', 'user_portal', 'portal', ['portal', 'portal_receiver'], ['tgid', 'tg_receiver'], onupdate='CASCADE', ondelete='CASCADE')


def downgrade():
    op.drop_constraint('user_portal_portal_fkey', 'user_portal', type_='foreignkey')
    op.drop_constraint('user_portal_user_fkey', 'user_portal', type_='foreignkey')
    op.create_foreign_key('user_portal_portal_fkey', 'user_portal', 'portal', ['portal', 'portal_receiver'], ['tgid', 'tg_receiver'])
    op.create_foreign_key('user_portal_user_fkey', 'user_portal', 'user', ['user'], ['tgid'])
