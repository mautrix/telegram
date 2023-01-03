# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2022 Tulir Asokan, Sumner Evans
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

from typing import TYPE_CHECKING, Any, ClassVar
from datetime import datetime, timedelta
from enum import Enum
import json

from asyncpg import Record
from attr import dataclass

from mautrix.types import UserID
from mautrix.util.async_db import Connection, Database

from ..types import TelegramID

fake_db = Database.create("") if TYPE_CHECKING else None


class BackfillType(Enum):
    HISTORICAL = "historical"
    SYNC_DIALOG = "sync_dialog"


@dataclass
class Backfill:
    db: ClassVar[Database] = fake_db

    queue_id: int | None
    user_mxid: UserID
    priority: int
    type: BackfillType
    portal_tgid: TelegramID
    portal_tg_receiver: TelegramID
    anchor_msg_id: TelegramID | None
    extra_data: dict[str, Any]
    messages_per_batch: int
    post_batch_delay: int
    max_batches: int
    dispatch_time: datetime | None
    completed_at: datetime | None
    cooldown_timeout: datetime | None

    @staticmethod
    def new(
        user_mxid: UserID,
        priority: int,
        type: BackfillType,
        portal_tgid: TelegramID,
        portal_tg_receiver: TelegramID,
        messages_per_batch: int,
        anchor_msg_id: TelegramID | None = None,
        extra_data: dict[str, Any] | None = None,
        post_batch_delay: int = 0,
        max_batches: int = -1,
    ) -> "Backfill":
        return Backfill(
            queue_id=None,
            user_mxid=user_mxid,
            priority=priority,
            type=type,
            portal_tgid=portal_tgid,
            portal_tg_receiver=portal_tg_receiver,
            anchor_msg_id=anchor_msg_id,
            extra_data=extra_data or {},
            messages_per_batch=messages_per_batch,
            post_batch_delay=post_batch_delay,
            max_batches=max_batches,
            dispatch_time=None,
            completed_at=None,
            cooldown_timeout=None,
        )

    @classmethod
    def _from_row(cls, row: Record | None) -> Backfill | None:
        if row is None:
            return None
        data = {**row}
        type = BackfillType(data.pop("type"))
        extra_data = json.loads(data.pop("extra_data", None) or "{}")
        return cls(**data, type=type, extra_data=extra_data)

    columns = [
        "user_mxid",
        "priority",
        "type",
        "portal_tgid",
        "portal_tg_receiver",
        "anchor_msg_id",
        "extra_data",
        "messages_per_batch",
        "post_batch_delay",
        "max_batches",
        "dispatch_time",
        "completed_at",
        "cooldown_timeout",
    ]
    columns_str = ",".join(columns)

    @classmethod
    async def get_next(cls, user_mxid: UserID) -> Backfill | None:
        q = f"""
        SELECT queue_id, {cls.columns_str}
        FROM backfill_queue
        WHERE user_mxid=$1
            AND (
                dispatch_time IS NULL
                OR (
                    dispatch_time < $2
                    AND completed_at IS NULL
                )
            )
            AND (
                cooldown_timeout IS NULL
                OR cooldown_timeout < current_timestamp
            )
        ORDER BY priority, queue_id
        LIMIT 1
        """
        return cls._from_row(
            await cls.db.fetchrow(q, user_mxid, datetime.now() - timedelta(minutes=15))
        )

    @classmethod
    async def delete_existing(
        cls,
        user_mxid: UserID,
        portal_tgid: int,
        portal_tg_receiver: int,
        type: BackfillType,
    ) -> Backfill | None:
        q = f"""
        WITH deleted_entries AS (
            DELETE FROM backfill_queue
            WHERE user_mxid=$1
              AND portal_tgid=$2
              AND portal_tg_receiver=$3
              AND type=$4
              AND dispatch_time IS NULL
              AND completed_at IS NULL
            RETURNING 1
        )
        WITH dispatched_entries AS (
            SELECT 1 FROM backfill_queue
            WHERE user_mxid=$1
              AND portal_tgid=$2
              AND portal_tg_receiver=$3
              AND type=$4
              AND dispatch_time IS NOT NULL
              AND completed_at IS NULL
        )
        """
        return cls._from_row(
            await cls.db.fetchrow(q, user_mxid, portal_tgid, portal_tg_receiver, type.value)
        )

    @classmethod
    async def delete_all(cls, user_mxid: UserID, conn: Connection | None = None) -> None:
        await (conn or cls.db).execute("DELETE FROM backfill_queue WHERE user_mxid=$1", user_mxid)

    @classmethod
    async def delete_for_portal(cls, tgid: int, tg_receiver: int) -> None:
        q = "DELETE FROM backfill_queue WHERE portal_tgid=$1 AND portal_tg_receiver=$2"
        await cls.db.execute(q, tgid, tg_receiver)

    async def insert(self) -> list[Backfill]:
        delete_q = f"""
        DELETE FROM backfill_queue
        WHERE user_mxid=$1
          AND portal_tgid=$2
          AND portal_tg_receiver=$3
          AND type=$4
          AND dispatch_time IS NULL
          AND completed_at IS NULL
        RETURNING queue_id, {self.columns_str}
        """
        q = f"""
        INSERT INTO backfill_queue ({self.columns_str})
        VALUES ({','.join(f'${i+1}' for i in range(len(self.columns)))})
        RETURNING queue_id
        """
        async with self.db.acquire() as conn, conn.transaction():
            deleted_rows = await conn.fetch(
                delete_q,
                self.user_mxid,
                self.portal_tgid,
                self.portal_tg_receiver,
                self.type.value,
            )
            self.queue_id = await conn.fetchval(
                q,
                self.user_mxid,
                self.priority,
                self.type.value,
                self.portal_tgid,
                self.portal_tg_receiver,
                self.anchor_msg_id,
                json.dumps(self.extra_data) if self.extra_data else None,
                self.messages_per_batch,
                self.post_batch_delay,
                self.max_batches,
                self.dispatch_time,
                self.completed_at,
                self.cooldown_timeout,
            )
        return [self._from_row(row) for row in deleted_rows]

    async def mark_dispatched(self) -> None:
        q = "UPDATE backfill_queue SET dispatch_time=$1 WHERE queue_id=$2"
        await self.db.execute(q, datetime.now(), self.queue_id)

    async def mark_done(self) -> None:
        q = "UPDATE backfill_queue SET completed_at=$1 WHERE queue_id=$2"
        await self.db.execute(q, datetime.now(), self.queue_id)

    async def set_cooldown_timeout(self, timeout: int) -> None:
        """
        Set the backfill request to cooldown for ``timeout`` seconds.
        """
        q = "UPDATE backfill_queue SET cooldown_timeout=$1 WHERE queue_id=$2"
        await self.db.execute(q, datetime.now() + timedelta(seconds=timeout), self.queue_id)
