"""Switch Telegram IDs to bigints

Revision ID: ec1d3dcc77e9
Revises: 990f4395afc6
Create Date: 2021-03-09 21:36:58.443727

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'ec1d3dcc77e9'
down_revision = '990f4395afc6'
branch_labels = None
depends_on = None


columns_to_upgrade = (
    ("bot_chat", "id"),
    ("message", "tgid"),
    ("message", "tg_space"),
    ("portal", "tgid"),
    ("portal", "tg_receiver"),
    ("puppet", "id"),
    ("puppet", "displayname_source"),
    ("user", "tgid"),
    ("user_portal", "user"),
    ("user_portal", "portal"),
    ("user_portal", "portal_receiver"),
    ("contact", "user"),
    ("contact", "contact"),
)


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        for table, column in columns_to_upgrade:
            op.alter_column(table, column, existing_type=sa.Integer, type_=sa.BigInteger)


def downgrade():
    if op.get_context().dialect.name == "postgresql":
        for table, column in columns_to_upgrade:
            op.alter_column(table, column, existing_type=sa.BigInteger, type_=sa.Integer)
