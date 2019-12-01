"""Switch mx_user_profile to native enum

Revision ID: 4f7d7ed5792a
Revises: 9e9c89b0b877
Create Date: 2019-08-04 17:47:36.568120

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '4f7d7ed5792a'
down_revision = '9e9c89b0b877'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute("UPDATE mx_user_profile SET membership=UPPER(membership)")
    conn.execute("UPDATE mx_user_profile SET membership='LEAVE' WHERE membership='LEFT'")


def downgrade():
    conn = op.get_bind()
    conn.execute("UPDATE mx_user_profile SET membership=LOWER(membership)")
