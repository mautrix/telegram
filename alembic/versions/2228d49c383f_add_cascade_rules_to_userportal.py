"""Add cascade rules to UserPortal

Revision ID: 2228d49c383f
Revises: bcfefa1f1299
Create Date: 2018-05-31 11:11:59.482112

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '2228d49c383f'
down_revision = 'bcfefa1f1299'
branch_labels = None
depends_on = None


def upgrade():
    try:
        with op.batch_alter_table("user_portal") as batch_op:
            batch_op.drop_constraint("user_portal_user_fkey", type_="foreignkey")
            batch_op.drop_constraint("user_portal_portal_fkey", type_="foreignkey")
            batch_op.create_foreign_key("user_portal_user_fkey", "user", ["user"], ["tgid"],
                                        onupdate="CASCADE", ondelete="CASCADE")
            batch_op.create_foreign_key("user_portal_portal_fkey", "portal",
                                        ["portal", "portal_receiver"], ["tgid", "tg_receiver"],
                                        onupdate="CASCADE", ondelete="CASCADE")
    except ValueError:
        return


def downgrade():
    try:
        with op.batch_alter_table("user_portal") as batch_op:
                batch_op.drop_constraint("user_portal_user_fkey", type_="foreignkey")
                batch_op.drop_constraint("user_portal_portal_fkey", type_="foreignkey")
                batch_op.create_foreign_key("user_portal_user_fkey", "portal",
                                            ["portal", "portal_receiver"], ["tgid", "tg_receiver"])
                batch_op.create_foreign_key("user_portal_portal_fkey", "user", ["user"], ["tgid"])
    except ValueError:
        return
