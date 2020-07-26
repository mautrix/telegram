from typing import Union
import argparse

from sqlalchemy import orm
from sqlalchemy.ext.declarative import declarative_base
import sqlalchemy as sql

from alchemysession import AlchemySessionContainer

parser = argparse.ArgumentParser(description="mautrix-telegram dbms migration script",
                                 prog="python -m mautrix_telegram.scripts.dbms_migrate")
parser.add_argument("-f", "--from-url", type=str, required=True, metavar="<url>",
                    help="the old database path")
parser.add_argument("-t", "--to-url", type=str, required=True, metavar="<url>",
                    help="the new database path")
parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logs while migrating")
args = parser.parse_args()
verbose = args.verbose or False


def log(message, end="\n"):
    if verbose:
        print(message, end=end, flush=True)


def connect(to):
    from mautrix.util.db import Base
    from mautrix.client.state_store.sqlalchemy import RoomState, UserProfile
    from mautrix_telegram.db import (Portal, Message, UserPortal, User, Contact, Puppet, BotChat,
                                     TelegramFile)

    db_engine = sql.create_engine(to)
    db_factory = orm.sessionmaker(bind=db_engine)
    db_session: Union[orm.Session, orm.scoped_session] = orm.scoped_session(db_factory)
    Base.metadata.bind = db_engine

    new_base = declarative_base()
    new_base.metadata.bind = db_engine
    session_container = AlchemySessionContainer(engine=db_engine, session=db_session,
                                                table_base=new_base, table_prefix="telethon_",
                                                manage_tables=False)

    return db_session, {
        "Version": session_container.Version,
        "Session": session_container.Session,
        "Entity": session_container.Entity,
        "SentFile": session_container.SentFile,
        "UpdateState": session_container.UpdateState,
        "Portal": Portal,
        "Message": Message,
        "Puppet": Puppet,
        "User": User,
        "UserPortal": UserPortal,
        "RoomState": RoomState,
        "UserProfile": UserProfile,
        "Contact": Contact,
        "BotChat": BotChat,
        "TelegramFile": TelegramFile,
    }


log("Connecting to old database")
session, tables = connect(args.from_url)

data = {}
for name, table in tables.items():
    log("Reading table {name}...".format(name=name), end=" ")
    data[name] = session.query(table).all()
    log("Done!")

log("Connecting to new database")
session, tables = connect(args.to_url)

for name, table in tables.items():
    log("Writing table {name}".format(name=name), end="")
    length = len(data[name])
    n = 0
    for row in data[name]:
        session.merge(row)
        n += 5
        if n >= length:
            log(".", end="")
            n = 0
    log(" Done!")

log("Committing changes to database...", end=" ")
session.commit()
log("Done!")
