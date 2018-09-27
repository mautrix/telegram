import sqlalchemy as sa
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class ChatLink(Base):
    __tablename__ = "chat_link"

    id = sa.Column(sa.Integer, primary_key=True)
    matrix_room = sa.Column(sa.String)
    tg_room = sa.Column(sa.BigInteger)
    active = sa.Column(sa.Boolean)


class TgUser(Base):
    __tablename__ = "tg_user"

    id = sa.Column(sa.Integer, primary_key=True)
    tg_id = sa.Column(sa.BigInteger)
    name = sa.Column(sa.String)
    profile_pic_id = sa.Column(sa.String, nullable=True)


class MatrixUser(Base):
    __tablename__ = "matrix_user"

    id = sa.Column(sa.Integer, primary_key=True)
    matrix_id = sa.Column(sa.String)
    name = sa.Column(sa.String)


class Message(Base):
    """Describes a message in a room bridged between Telegram and Matrix"""
    __tablename__ = "message"

    id = sa.Column(sa.Integer, primary_key=True)
    tg_group_id = sa.Column(sa.BigInteger)
    tg_message_id = sa.Column(sa.BigInteger)

    matrix_room_id = sa.Column(sa.String)
    matrix_event_id = sa.Column(sa.String)

    displayname = sa.Column(sa.String)
