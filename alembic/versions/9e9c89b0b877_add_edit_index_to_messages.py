"""Add edit index to messages

Revision ID: 9e9c89b0b877
Revises: 17574c57f3f8
Create Date: 2019-05-29 15:28:23.128377

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9e9c89b0b877'
down_revision = '17574c57f3f8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('_message_temp',
                    sa.Column('mxid', sa.String),
                    sa.Column('mx_room', sa.String),
                    sa.Column('tgid', sa.Integer),
                    sa.Column('tg_space', sa.Integer),
                    sa.Column('edit_index', sa.Integer),
                    sa.PrimaryKeyConstraint('tgid', 'tg_space', 'edit_index'),
                    sa.UniqueConstraint("mxid", "mx_room", "tg_space", name="_mx_id_room_2"))
    c = op.get_bind()
    c.execute("INSERT INTO _message_temp (mxid, mx_room, tgid, tg_space, edit_index) "
              "SELECT message.mxid, message.mx_room, message.tgid, message.tg_space, 0 "
              "FROM message")
    c.execute("DROP TABLE message")
    c.execute("ALTER TABLE _message_temp RENAME TO message")


def downgrade():
    op.create_table('_message_temp',
                    sa.Column('mxid', sa.String),
                    sa.Column('mx_room', sa.String),
                    sa.Column('tgid', sa.Integer),
                    sa.Column('tg_space', sa.Integer),
                    sa.PrimaryKeyConstraint('tgid', 'tg_space'),
                    sa.UniqueConstraint("mxid", "mx_room", "tg_space", name="_mx_id_room"))
    c = op.get_bind()
    c.execute("INSERT INTO _message_temp (mxid, mx_room, tgid, tg_space) "
              "SELECT message.mxid, message.mx_room, message.tgid, message.tg_space "
              "FROM message")
    c.execute("DROP TABLE message")
    c.execute("ALTER TABLE _message_temp RENAME TO message")
