"""Move state store to main database

Revision ID: 6ca3d74d51e4
Revises: 2228d49c383f
Create Date: 2018-06-26 21:31:26.911307

"""
import json
import re

from alembic import context, op
import sqlalchemy.orm as orm
import sqlalchemy as sa

from mautrix.util.db import Base

from mautrix_telegram.config import Config

# revision identifiers, used by Alembic.
revision = "6ca3d74d51e4"
down_revision = "2228d49c383f"
branch_labels = None
depends_on = None


class RoomState(Base):
    __tablename__ = "mx_room_state"
    __table_args__ = {"extend_existing": True}

    room_id = sa.Column(sa.String, primary_key=True)
    power_levels = sa.Column("power_levels", sa.Text, nullable=True)


class UserProfile(Base):
    __tablename__ = "mx_user_profile"
    __table_args__ = {"extend_existing": True}

    room_id = sa.Column(sa.String, primary_key=True)
    user_id = sa.Column(sa.String, primary_key=True)
    membership = sa.Column(sa.String, nullable=False, default="leave")
    displayname = sa.Column(sa.String, nullable=True)
    avatar_url = sa.Column(sa.String, nullable=True)


class Puppet(Base):
    __tablename__ = "puppet"
    __table_args__ = {"extend_existing": True}

    id = sa.Column(sa.Integer, primary_key=True)
    displayname = sa.Column(sa.String, nullable=True)
    displayname_source = sa.Column(sa.Integer, nullable=True)
    username = sa.Column(sa.String, nullable=True)
    photo_id = sa.Column(sa.String, nullable=True)
    is_bot = sa.Column(sa.Boolean, nullable=True)
    matrix_registered = sa.Column(sa.Boolean, nullable=False, default=False)


def upgrade():
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.add_column(sa.Column("matrix_registered", sa.Boolean(), nullable=False,
                                      server_default=sa.sql.expression.false()))
    op.create_table("mx_room_state",
                    sa.Column("room_id", sa.String(), nullable=False),
                    sa.Column("power_levels", sa.Text(), nullable=True),
                    sa.PrimaryKeyConstraint("room_id"))
    op.create_table("mx_user_profile",
                    sa.Column("room_id", sa.String(), nullable=False),
                    sa.Column("user_id", sa.String(), nullable=False),
                    sa.Column("membership", sa.String(), nullable=False,
                              default="leave"),
                    sa.Column("displayname", sa.String(), nullable=True),
                    sa.Column("avatar_url", sa.String(), nullable=True),
                    sa.PrimaryKeyConstraint("room_id", "user_id"))

    try:
        migrate_state_store()
    except Exception as e:
        print("Failed to migrate state store:", e)
        print("Migrating the state store isn't required, but you can retry by alembic downgrading "
              "to revision 2228d49c383f and upgrading again.")


def migrate_state_store():
    conn = op.get_bind()
    session: orm.Session = orm.sessionmaker(bind=conn)()

    try:
        with open("mx-state.json") as file:
            data = json.load(file)
    except FileNotFoundError:
        return
    if not data:
        return
    registrations = data.get("registrations", [])

    mxtg_config_path = context.get_x_argument(as_dictionary=True).get("config", "config.yaml")
    mxtg_config = Config(mxtg_config_path, None, None)
    mxtg_config.load()

    username_template = mxtg_config.get("bridge.username_template", "telegram_{userid}")
    hs_domain = mxtg_config["homeserver.domain"]
    localpart = username_template.format(userid="(.+)")
    mxid_regex = re.compile("@{}:{}".format(localpart, hs_domain))
    for user in registrations:
        match = mxid_regex.match(user)
        if not match:
            continue

        puppet = session.query(Puppet).get(match.group(1))
        if not puppet:
            continue

        puppet.matrix_registered = True
        session.merge(puppet)
    session.commit()

    user_profiles = [UserProfile(room_id=room, user_id=user,
                                 membership=member.get("membership", "leave"),
                                 displayname=member.get("displayname", None),
                                 avatar_url=member.get("avatar_url", None))
                     for room, members in data.get("members", {}).items()
                     for user, member in members.items()]
    session.add_all(user_profiles)
    session.commit()

    room_state = [RoomState(room_id=room, power_levels=json.dumps(levels))
                  for room, levels in data.get("power_levels", {}).items()]
    session.add_all(room_state)
    session.commit()


def downgrade():
    op.drop_table("mx_user_profile")
    op.drop_table("mx_room_state")
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.drop_column("matrix_registered")
