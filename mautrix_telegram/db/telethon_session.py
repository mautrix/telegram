# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
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
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Iterable
import asyncio
import datetime

from telethon import utils
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession
from telethon.tl.types import PeerChannel, PeerChat, PeerUser, updates

from mautrix.util.async_db import Database, Scheme

fake_db = Database.create("") if TYPE_CHECKING else None


class PgSession(MemorySession):
    db: ClassVar[Database] = fake_db

    session_id: str
    _dc_id: int
    _server_address: str | None
    _port: int | None
    _auth_key: AuthKey | None
    _takeout_id: int | None
    _process_entities_lock: asyncio.Lock

    def __init__(
        self,
        session_id: str,
        dc_id: int = 0,
        server_address: str | None = None,
        port: int | None = None,
        auth_key: AuthKey | None = None,
        takeout_id: int | None = None,
    ) -> None:
        super().__init__()
        self.session_id = session_id
        self._dc_id = dc_id
        self._server_address = server_address
        self._port = port
        self._auth_key = auth_key
        self._takeout_id = takeout_id
        self._process_entities_lock = asyncio.Lock()

    def clone(self, to_instance=None) -> MemorySession:
        # We don't want to store data of clones
        # (which are used for temporarily connecting to different DCs)
        return super().clone(MemorySession())

    @property
    def auth_key_bytes(self) -> bytes | None:
        return self._auth_key.key if self._auth_key else None

    @classmethod
    async def get(cls, session_id: str) -> PgSession:
        q = (
            "SELECT session_id, dc_id, server_address, port, auth_key FROM telethon_sessions "
            "WHERE session_id=$1"
        )
        row = await cls.db.fetchrow(q, session_id)
        if row is None:
            return cls(session_id)
        data = {**row}
        auth_key = AuthKey(data.pop("auth_key", None))
        return cls(**data, auth_key=auth_key)

    @classmethod
    async def has(cls, session_id: str) -> bool:
        q = "SELECT COUNT(*) FROM telethon_sessions WHERE session_id=$1"
        count = await cls.db.fetchval(q, session_id)
        return count > 0

    async def save(self) -> None:
        q = (
            "INSERT INTO telethon_sessions (session_id, dc_id, server_address, port, auth_key) "
            "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (session_id) "
            "DO UPDATE SET dc_id=$2, server_address=$3, port=$4, auth_key=$5"
        )
        await self.db.execute(
            q, self.session_id, self.dc_id, self.server_address, self.port, self.auth_key_bytes
        )

    _tables: ClassVar[tuple[str, ...]] = (
        "telethon_sessions",
        "telethon_entities",
        "telethon_sent_files",
        "telethon_update_state",
    )

    async def delete(self) -> None:
        async with self.db.acquire() as conn, conn.transaction():
            for table in self._tables:
                await conn.execute(f"DELETE FROM {table} WHERE session_id=$1", self.session_id)

    async def close(self) -> None:
        # Nothing to do here, DB connection is global
        pass

    async def get_update_state(self, entity_id: int) -> updates.State | None:
        q = (
            "SELECT pts, qts, date, seq, unread_count FROM telethon_update_state "
            "WHERE session_id=$1 AND entity_id=$2"
        )
        row = await self.db.fetchrow(q, self.session_id, entity_id)
        if row is None:
            return None
        date = datetime.datetime.utcfromtimestamp(row["date"])
        return updates.State(row["pts"], row["qts"], date, row["seq"], row["unread_count"])

    async def set_update_state(self, entity_id: int, row: updates.State) -> None:
        q = """
        INSERT INTO telethon_update_state(session_id, entity_id, pts, qts, date, seq, unread_count)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (session_id, entity_id) DO UPDATE SET
            pts=excluded.pts, qts=excluded.qts, date=excluded.date, seq=excluded.seq,
            unread_count=excluded.unread_count
        """
        ts = row.date.timestamp()
        await self.db.execute(
            q, self.session_id, entity_id, row.pts, row.qts, ts, row.seq, row.unread_count
        )

    async def delete_update_state(self, entity_id: int) -> None:
        q = "DELETE FROM telethon_update_state WHERE session_id=$1 AND entity_id=$2"
        await self.db.execute(q, self.session_id, entity_id)

    async def get_update_states(self) -> Iterable[tuple[int, updates.State], ...]:
        q = (
            "SELECT entity_id, pts, qts, date, seq, unread_count FROM telethon_update_state "
            "WHERE session_id=$1"
        )
        rows = await self.db.fetch(q, self.session_id)
        return (
            (
                row["entity_id"],
                updates.State(
                    row["pts"],
                    row["qts"],
                    datetime.datetime.utcfromtimestamp(row["date"]),
                    row["seq"],
                    row["unread_count"],
                ),
            )
            for row in rows
        )

    def _entity_values_to_row(
        self, id: int, hash: int, username: str | None, phone: str | int | None, name: str | None
    ) -> tuple[str, int, int, str | None, str | None, str | None]:
        return self.session_id, id, hash, username, str(phone) if phone else None, name

    async def process_entities(self, tlo) -> None:
        # Postgres likes to deadlock on simultaneous upserts, so just lock the whole thing here
        # TODO: make sure postgres doesn't deadlock on upserts when session_id is different
        async with self._process_entities_lock:
            await self._locked_process_entities(tlo)

    async def _locked_process_entities(self, tlo) -> None:
        rows: list[
            tuple[str, int, int, str | None, str | None, str | None]
        ] = self._entities_to_rows(tlo)
        if not rows:
            return
        if self.db.scheme == Scheme.POSTGRES:
            q = (
                "INSERT INTO telethon_entities (session_id, id, hash, username, phone, name) "
                "VALUES ($1, unnest($2::bigint[]), unnest($3::bigint[]), "
                "        unnest($4::text[]), unnest($5::text[]), unnest($6::text[])) "
                "ON CONFLICT (session_id, id) DO UPDATE"
                "  SET hash=excluded.hash, username=excluded.username,"
                "      phone=excluded.phone, name=excluded.name"
            )
            _, ids, hashes, usernames, phones, names = zip(*rows)
            await self.db.execute(q, self.session_id, ids, hashes, usernames, phones, names)
        else:
            q = (
                "INSERT INTO telethon_entities (session_id, id, hash, username, phone, name) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (session_id, id) DO UPDATE "
                "    SET hash=$3, username=$4, phone=$5, name=$6"
            )
            await self.db.executemany(q, rows)

    async def _select_entity(
        self, constraint: str, *args: str | int | tuple[int, ...]
    ) -> tuple[int, int] | None:
        q = f"SELECT id, hash FROM telethon_entities WHERE session_id=$1 AND {constraint}"
        row = await self.db.fetchrow(q, self.session_id, *args)
        if row is None:
            return None
        return row["id"], row["hash"]

    async def get_entity_rows_by_phone(self, key: str | int) -> tuple[int, int] | None:
        return await self._select_entity("phone=$2", str(key))

    async def get_entity_rows_by_username(self, key: str) -> tuple[int, int] | None:
        return await self._select_entity("username=$2", key)

    async def get_entity_rows_by_name(self, key: str) -> tuple[int, int] | None:
        return await self._select_entity("name=$2", key)

    async def get_entity_rows_by_id(self, key: int, exact: bool = True) -> tuple[int, int] | None:
        if exact:
            return await self._select_entity("id=$2", key)

        ids = (
            utils.get_peer_id(PeerUser(key)),
            utils.get_peer_id(PeerChat(key)),
            utils.get_peer_id(PeerChannel(key)),
        )
        if self.db.scheme in (Scheme.POSTGRES, Scheme.COCKROACH):
            return await self._select_entity("id=ANY($2)", ids)
        else:
            return await self._select_entity(f"id IN ($2, $3, $4)", *ids)
