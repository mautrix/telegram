# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2019 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from typing import Dict
import argparse

from sqlalchemy import orm
import sqlalchemy as sql

from mautrix.util.db import Base

from mautrix_telegram.db import Portal, Message, Puppet, BotChat
from mautrix_telegram.config import Config

from .models import ChatLink, TgUser, MatrixUser, Message as TMMessage, Base as TelematrixBase

parser = argparse.ArgumentParser(
    description="mautrix-telegram telematrix import script",
    prog="python -m mautrix_telegram.scripts.telematrix_import")
parser.add_argument("-c", "--config", type=str, default="config.yaml",
                    metavar="<path>", help="the path to your mautrix-telegram config file")
parser.add_argument("-b", "--bot-id", type=int, required=True,
                    metavar="<id>", help="the telegram user ID of your relay bot")
parser.add_argument("-t", "--telematrix-database", type=str, default="sqlite:///database.db",
                    metavar="<url>", help="your telematrix database URL")
args = parser.parse_args()

config = Config(args.config, None, None)
config.load()

mxtg_db_engine = sql.create_engine(config["appservice.database"])
mxtg = orm.sessionmaker(bind=mxtg_db_engine)()
Base.metadata.bind = mxtg_db_engine

telematrix_db_engine = sql.create_engine(args.telematrix_database)
telematrix = orm.sessionmaker(bind=telematrix_db_engine)()
TelematrixBase.metadata.bind = telematrix_db_engine

chat_links = telematrix.query(ChatLink).all()
tg_users = telematrix.query(TgUser).all()
mx_users = telematrix.query(MatrixUser).all()
tm_messages = telematrix.query(TMMessage).all()

telematrix.close()
telematrix_db_engine.dispose()

portals_by_tgid: Dict[int, Portal] = {}
portals_by_mxid: Dict[str, Portal] = {}
chats: Dict[int, BotChat] = {}
messages: Dict[str, Message] = {}
puppets: Dict[int, Puppet] = {}

for chat_link in chat_links:
    if type(chat_link.tg_room) is str:
        print(f"Expected tg_room to be a number, got a string. Ignoring {chat_link.tg_room}")
        continue
    if chat_link.tg_room >= 0:
        print(f"Unexpected unprefixed telegram chat ID: {chat_link.tg_room}, ignoring...")
        continue
    tgid = str(chat_link.tg_room)
    if tgid.startswith("-100"):
        tgid = int(tgid[4:])
        peer_type = "channel"
        megagroup = True
    else:
        tgid = -chat_link.tg_room
        peer_type = "chat"
        megagroup = False

    portal = Portal(tgid=tgid, tg_receiver=tgid, peer_type=peer_type, megagroup=megagroup,
                    mxid=chat_link.matrix_room)
    chats[tgid] = BotChat(id=tgid, type=peer_type)
    if chat_link.tg_room in portals_by_tgid:
        print(f"Warning: Ignoring bridge from {portal.tgid} to {portal.mxid} "
              f"in favor of {portals_by_tgid[portal.tgid].mxid}")
        continue
    elif chat_link.matrix_room in portals_by_mxid:
        print(f"Warning: Ignoring bridge from {portal.mxid} to {portal.tgid} "
              f"in favor of {portals_by_mxid[portal.mxid].tgid}")
        continue
    portals_by_tgid[portal.tgid] = portal
    portals_by_mxid[portal.mxid] = portal

for tm_msg in tm_messages:
    try:
        portal = portals_by_tgid[tm_msg.tg_group_id]
    except KeyError:
        print(f"Found message entry {tm_msg.tg_message_id} in unlinked chat {tm_msg.tg_group_id},"
              " ignoring...")
        continue
    if tm_msg.matrix_room_id != portal.mxid:
        print(f"Found message entry {tm_msg.tg_message_id} with "
              f"mismatching matrix room ID {tm_msg.matrix_room_id} (expected {portal.mxid})")
        continue
    tg_space = portal.tgid if portal.peer_type == "channel" else args.bot_id
    message = Message(mxid=tm_msg.matrix_event_id, mx_room=tm_msg.matrix_room_id,
                      tgid=tm_msg.tg_message_id, tg_space=tg_space)
    messages[tm_msg.matrix_event_id] = message

for user in tg_users:
    puppets[user.tg_id] = Puppet(id=user.tg_id, displayname=user.name,
                                 displayname_source=args.bot_id)

for k, v in portals_by_tgid.items():
    mxtg.add(v)
for k, v in chats.items():
    mxtg.add(v)
for k, v in messages.items():
    mxtg.add(v)
for k, v in puppets.items():
    mxtg.add(v)

mxtg.commit()
