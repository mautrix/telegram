"""Add bot_id to BotChat

Revision ID: 6e8b438b5e84
Revises: 2228d49c383f
Create Date: 2018-06-22 16:32:31.922480

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "6e8b438b5e84"
down_revision = "2228d49c383f"
branch_labels = None
depends_on = None

convention = {
    "pk": "pk_%(table_name)s"
}

metadata = sa.MetaData(naming_convention=convention)


def upgrade():
    conn = op.get_bind()
    res = conn.execute("SELECT id, `type` FROM bot_chat")
    results = res.fetchall()
    op.drop_table("bot_chat")
    bot_chat = op.create_table('bot_chat',
                               sa.Column('bot_id', sa.Integer),
                               sa.Column('chat_id', sa.Integer),
                               sa.Column('type', sa.String, nullable=False),
                               sa.PrimaryKeyConstraint('bot_id', 'chat_id'))
    op.bulk_insert(bot_chat, [{"bot_id": 0, "chat_id": r[0], "type": r[1]} for r in results])


def downgrade():
    conn = op.get_bind()
    res = conn.execute("SELECT chat_id, `type` FROM bot_chat WHERE bot_id=0")
    results = res.fetchall()
    op.drop_table("bot_chat")
    bot_chat = op.create_table('bot_chat',
                               sa.Column('id', sa.Integer),
                               sa.Column('type', sa.String, nullable=False),
                               sa.PrimaryKeyConstraint('id'))
    op.bulk_insert(bot_chat, [{"id": r[0], "type": r[1]} for r in results])
