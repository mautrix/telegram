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
)
