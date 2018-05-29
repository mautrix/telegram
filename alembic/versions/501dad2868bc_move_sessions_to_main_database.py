"""Move sessions to main database

Revision ID: 501dad2868bc
Revises: 7d47d84380b6
Create Date: 2018-03-02 19:15:53.826985

"""
from alembic import op
import sqlalchemy as sa
import sqlite3
import os

# revision identifiers, used by Alembic.
revision = '501dad2868bc'
down_revision = '7d47d84380b6'
branch_labels = None
depends_on = None


def upgrade():
    Session = op.create_table('telethon_sessions',
                              sa.Column('session_id', sa.String, nullable=False),
                              sa.Column('dc_id', sa.Integer, nullable=False),
                              sa.Column('server_address', sa.String, nullable=True),
                              sa.Column('port', sa.Integer, nullable=True),
                              sa.Column('auth_key', sa.LargeBinary, nullable=True),
                              sa.PrimaryKeyConstraint('session_id', 'dc_id'))
    SentFile = op.create_table('telethon_sent_files',
                               sa.Column('session_id', sa.String, nullable=False),
                               sa.Column('md5_digest', sa.LargeBinary, nullable=False),
                               sa.Column('file_size', sa.Integer, nullable=False),
                               sa.Column('type', sa.Integer, nullable=False),
                               sa.Column('id', sa.BigInteger, nullable=True),
                               sa.Column('hash', sa.BigInteger, nullable=True),
                               sa.PrimaryKeyConstraint('session_id', 'md5_digest', 'file_size',
                                                       'type'))
    Entity = op.create_table('telethon_entities',
                             sa.Column('session_id', sa.String, nullable=False),
                             sa.Column('id', sa.Integer, nullable=False),
                             sa.Column('hash', sa.Integer, nullable=False),
                             sa.Column('username', sa.String, nullable=True),
                             sa.Column('phone', sa.Integer, nullable=True),
                             sa.Column('name', sa.String, nullable=True),
                             sa.PrimaryKeyConstraint('session_id', 'id'))
    Version = op.create_table('telethon_version',
                              sa.Column('version', sa.Integer, nullable=False),
                              sa.PrimaryKeyConstraint('version'))
    conn = op.get_bind()
    sessions = [os.path.basename(f) for f in os.listdir(".") if f.endswith(".session")]
    for session in sessions:
        session_to_sqlalchemy(conn, session, Session, SentFile, Entity)


def session_to_sqlalchemy(conn, path, Session, SentFile, Entity):
    session_conn = sqlite3.connect(path)
    session_id = os.path.splitext(path)[0]
    c = session_conn.cursor()

    auth_data_tuples = c.execute("SELECT * FROM sessions").fetchall()
    auth_data_dicts = []
    for row in auth_data_tuples:
        dc_id, server_address, port, auth_key = row
        auth_data_dicts.append({
            "session_id": session_id,
            "dc_id": dc_id,
            "server_address": server_address,
            "port": port,
            "auth_key": auth_key,
        })
    if auth_data_dicts:
        conn.execute(Session.insert().values(auth_data_dicts))

    sent_file_tuples = c.execute("SELECT * FROM sent_files").fetchall()
    sent_file_dicts = []
    for row in sent_file_tuples:
        md5_digest, file_size, type, id, hash = row
        sent_file_dicts.append({
            "session_id": session_id,
            "md5_digest": md5_digest,
            "file_size": file_size,
            "type": type,
            "id": id,
            "hash": hash,
        })
    if sent_file_dicts:
        conn.execute(SentFile.insert().values(sent_file_dicts))

    entity_tuples = c.execute("SELECT * FROM entities").fetchall()
    entity_dicts = []
    for row in entity_tuples:
        id, hash, username, phone, name = row
        entity_dicts.append({
            "session_id": session_id,
            "id": id,
            "hash": hash,
            "username": username,
            "phone": phone,
            "name": name,
        })
    if entity_dicts:
        conn.execute(Entity.insert().values(entity_dicts))

    c.close()
    session_conn.close()


def downgrade():
    op.drop_table('telethon_version')
    op.drop_table('telethon_entities')
    op.drop_table('telethon_sent_files')
    op.drop_table('telethon_sessions')
