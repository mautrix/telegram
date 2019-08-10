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


def format_duration(seconds: int) -> str:
    def pluralize(count: int, singular: str) -> str:
        return singular if count == 1 else singular + "s"

    def include(count: int, word: str) -> str:
        return f"{count} {pluralize(count, word)}" if count > 0 else ""

    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = [a for a in [
        include(days, "day"),
        include(hours, "hour"),
        include(minutes, "minute"),
        include(seconds, "second")] if a]
    if len(parts) > 2:
        return "{} and {}".format(", ".join(parts[:-1]), parts[-1])
    return " and ".join(parts)
