from alembic import context
from sqlalchemy import engine_from_config, pool
from logging.config import fileConfig

import sys
from os.path import abspath, dirname

sys.path.insert(0, dirname(dirname(abspath(__file__))))

from mautrix_telegram.db import Base
from mautrix_telegram.config import Config
from alchemysession import AlchemySessionContainer

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

mxtg_config_path = context.get_x_argument(as_dictionary=True).get("config", "config.yaml")
mxtg_config = Config(mxtg_config_path, None, None)
mxtg_config.load()
config.set_main_option("sqlalchemy.url",
                       mxtg_config.get("appservice.database", "sqlite:///mautrix-telegram.db"))


class FakeDB:
    @staticmethod
    def query_property():
        return None


AlchemySessionContainer.create_table_classes(FakeDB(), "telethon_", Base)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
