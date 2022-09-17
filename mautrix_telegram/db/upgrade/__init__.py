from mautrix.util.async_db import UpgradeTable

upgrade_table = UpgradeTable()

from . import (
    v01_initial_revision,
    v02_sponsored_events,
    v03_reactions,
    v04_disappearing_messages,
    v05_channel_ghosts,
    v06_puppet_avatar_url,
    v07_puppet_phone_number,
    v08_portal_first_event,
    v09_puppet_username_index,
    v10_more_backfill_fields,
    v11_backfill_queue,
    v12_message_sender,
    v13_multiple_reactions,
)
