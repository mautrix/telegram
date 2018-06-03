"""initial revision

Revision ID: 97d2a942bcf8
Revises:
Create Date: 2018-02-11 18:40:55.483842

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '97d2a942bcf8'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('portal',
                    sa.Column('tgid', sa.Integer),
                    sa.Column('tg_receiver', sa.Integer),
                    sa.Column('peer_type', sa.String, nullable=False, default=""),
                    sa.Column('mxid', sa.String, nullable=True),
                    sa.Column('username', sa.String, nullable=True),
                    sa.Column('title', sa.String, nullable=True),
                    sa.Column('about', sa.String, nullable=True),
                    sa.Column('photo_id', sa.String, nullable=True),
                    sa.PrimaryKeyConstraint('tgid', 'tg_receiver'),
                    sa.UniqueConstraint('mxid'))
    op.create_table('user',
                    sa.Column('mxid', sa.String),
                    sa.Column('tgid', sa.Integer, nullable=True, unique=True),
                    sa.Column('tg_username', sa.String, nullable=True),
                    sa.Column('saved_contacts', sa.Integer, nullable=False, default=0),
                    sa.PrimaryKeyConstraint('mxid'))
    op.create_table('puppet',
                    sa.Column('id', sa.Integer),
                    sa.Column('displayname', sa.String, nullable=True),
                    sa.Column('username', sa.String, nullable=True),
                    sa.Column('photo_id', sa.String, nullable=True),
                    sa.PrimaryKeyConstraint('id'))
    op.create_table('contact',
                    sa.Column('user', sa.Integer),
                    sa.Column('contact', sa.Integer),
                    sa.ForeignKeyConstraint(("user",), ("user.tgid",)),
                    sa.ForeignKeyConstraint(("contact",), ("puppet.id",)),
                    sa.PrimaryKeyConstraint('user', 'contact'))
    op.create_table('user_portal',
                    sa.Column('user', sa.Integer),
                    sa.Column('portal', sa.Integer),
                    sa.Column('portal_receiver', sa.Integer),
                    sa.PrimaryKeyConstraint('user', 'portal', 'portal_receiver'),
                    sa.ForeignKeyConstraint(("user",), ("user.tgid",),
                                            name="user_portal_user_fkey",
                                            onupdate="CASCADE", ondelete="CASCADE"),
                    sa.ForeignKeyConstraint(("portal", "portal_receiver"),
                                            ("portal.tgid", "portal.tg_receiver"),
                                            name="user_portal_portal_fkey",
                                            onupdate="CASCADE", ondelete="CASCADE"))
    op.create_table('message',
                    sa.Column('mxid', sa.String),
                    sa.Column('mx_room', sa.String),
                    sa.Column('tgid', sa.Integer),
                    sa.Column('tg_space', sa.Integer),
                    sa.PrimaryKeyConstraint('tgid', 'tg_space'),
                    sa.UniqueConstraint("mxid", "mx_room", "tg_space", name="_mx_id_room"))
    op.create_table('bot_chat',
                    sa.Column('id', sa.Integer),
                    sa.Column('type', sa.String, nullable=False, default=""),
                    sa.PrimaryKeyConstraint('id'))


def downgrade():
    op.drop_table('bot_chat')
    op.drop_table('message')
    op.drop_table('user_portal')
    op.drop_table('contact')
    op.drop_table('puppet')
    op.drop_table('user')
    op.drop_table('portal')
