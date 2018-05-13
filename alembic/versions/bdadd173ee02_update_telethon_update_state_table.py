"""Update telethon update state table

Revision ID: bdadd173ee02
Revises: eeaf0dae87ce
Create Date: 2018-05-13 10:42:59.395597

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'bdadd173ee02'
down_revision = 'eeaf0dae87ce'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("telethon_entities") as batch_op:
        batch_op.alter_column("id", existing_type=sa.Integer, type_=sa.BigInteger)
        batch_op.alter_column("hash", existing_type=sa.Integer, type_=sa.BigInteger)

    with op.batch_alter_table("telethon_update_state") as batch_op:
        batch_op.alter_column("entity_id", existing_type=sa.Integer, type_=sa.BigInteger)
        batch_op.alter_column("pts", existing_type=sa.Integer, type_=sa.BigInteger)
        batch_op.alter_column("qts", existing_type=sa.Integer, type_=sa.BigInteger)
        batch_op.alter_column("date", existing_type=sa.Integer, type_=sa.BigInteger)
        batch_op.alter_column("seq", existing_type=sa.Integer, type_=sa.BigInteger)
        batch_op.add_column(sa.Column("unread_count", sa.Integer))


def downgrade():
    with op.batch_alter_table("telethon_entities") as batch_op:
        batch_op.alter_column("id", existing_type=sa.BigInteger, type_=sa.Integer)
        batch_op.alter_column("hash", existing_type=sa.BigInteger, type_=sa.Integer)

    with op.batch_alter_table("telethon_update_state") as batch_op:
        batch_op.alter_column("entity_id", existing_type=sa.BigInteger, type_=sa.Integer)
        batch_op.alter_column("pts", existing_type=sa.BigInteger, type_=sa.Integer)
        batch_op.alter_column("qts", existing_type=sa.BigInteger, type_=sa.Integer)
        batch_op.alter_column("date", existing_type=sa.BigInteger, type_=sa.Integer)
        batch_op.alter_column("seq", existing_type=sa.BigInteger, type_=sa.Integer)
        batch_op.drop_column("unread_count")
