# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
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
from typing import Coroutine, List
import argparse
import asyncio
import logging.config
import sys
import copy
import signal

from sqlalchemy import orm
import sqlalchemy as sql

from mautrix_appservice import AppService
from alchemysession import AlchemySessionContainer

from .web.provisioning import ProvisioningAPI
from .web.public import PublicBridgeWebsite
from .abstract_user import init as init_abstract_user
from .base import Base
from .bot import init as init_bot
from .config import Config
from .context import Context
from .db import init as init_db
from .formatter import init as init_formatter
from .matrix import MatrixHandler
from .portal import init as init_portal
from .puppet import init as init_puppet
from .sqlstatestore import SQLStateStore
from .user import User, init as init_user
from . import __version__

parser = argparse.ArgumentParser(
    description="A Matrix-Telegram puppeting bridge.",
    prog="python -m mautrix-telegram")
parser.add_argument("-c", "--config", type=str, default="config.yaml",
                    metavar="<path>", help="the path to your config file")
parser.add_argument("-b", "--base-config", type=str, default="example-config.yaml",
                    metavar="<path>", help="the path to the example config "
                                           "(for automatic config updates)")
parser.add_argument("-g", "--generate-registration", action="store_true",
                    help="generate registration and quit")
parser.add_argument("-r", "--registration", type=str, default="registration.yaml",
                    metavar="<path>", help="the path to save the generated registration to")
args = parser.parse_args()

config = Config(args.config, args.registration, args.base_config)
config.load()
config.update()

if args.generate_registration:
    config.generate_registration()
    config.save()
    print(f"Registration generated and saved to {config.registration_path}")
    sys.exit(0)

logging.config.dictConfig(copy.deepcopy(config["logging"]))
log = logging.getLogger("mau.init")  # type: logging.Logger
log.debug(f"Initializing mautrix-telegram {__version__}")

db_engine = sql.create_engine(config["appservice.database"] or "sqlite:///mautrix-telegram.db")
db_factory = orm.sessionmaker(bind=db_engine)
db_session = orm.scoping.scoped_session(db_factory)
Base.metadata.bind = db_engine

session_container = AlchemySessionContainer(engine=db_engine, session=db_session,
                                            table_base=Base, table_prefix="telethon_",
                                            manage_tables=False)
if config["appservice.sqlalchemy_core_mode"]:
    try:
        session_container.core_mode = True
    except AttributeError:
        log.error("Current version of teleton-session-sqlalchemy does not support core mode")

loop = asyncio.get_event_loop()  # type: asyncio.AbstractEventLoop

state_store = SQLStateStore(db_session)
mebibyte = 1024 ** 2
appserv = AppService(config["homeserver.address"], config["homeserver.domain"],
                     config["appservice.as_token"], config["appservice.hs_token"],
                     config["appservice.bot_username"], log="mau.as", loop=loop,
                     verify_ssl=config["homeserver.verify_ssl"], state_store=state_store,
                     real_user_content_key="net.maunium.telegram.puppet",
                     aiohttp_params={
                         "client_max_size": config["appservice.max_body_size"] * mebibyte
                     })

context = Context(appserv, db_session, config, loop, session_container)

if config["appservice.public.enabled"]:
    public_website = PublicBridgeWebsite(loop)
    appserv.app.add_subapp(config["appservice.public.prefix"] or "/public", public_website.app)
    context.public_website = public_website

if config["appservice.provisioning.enabled"]:
    provisioning_api = ProvisioningAPI(context)
    appserv.app.add_subapp(config["appservice.provisioning.prefix"] or "/_matrix/provisioning",
                           provisioning_api.app)
    context.provisioning_api = provisioning_api

with appserv.run(config["appservice.hostname"], config["appservice.port"]) as start:
    init_db(db_session, db_engine)
    init_abstract_user(context)
    context.bot = init_bot(context)
    context.mx = MatrixHandler(context)
    init_formatter(context)
    init_portal(context)
    startup_actions = (init_puppet(context) +
                       init_user(context) +
                       [start,
                        context.mx.init_as_bot()])  # type: List[Coroutine]

    if context.bot:
        startup_actions.append(context.bot.start())

    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    try:
        log.debug("Initialization complete, running startup actions")
        loop.run_until_complete(asyncio.gather(*startup_actions, loop=loop))
        log.debug("Startup actions complete, now running forever")
        loop.run_forever()
    except KeyboardInterrupt:
        log.debug("Interrupt received, stopping clients")
        loop.run_until_complete(
            asyncio.gather(*[user.stop() for user in User.by_tgid.values()], loop=loop))
        log.debug("Clients stopped, shutting down")
        sys.exit(0)
