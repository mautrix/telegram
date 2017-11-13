// mautrix-telegram - A Matrix-Telegram puppeting bridge
// Copyright (C) 2017 Tulir Asokan
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.
const TelegramPeer = require("./telegram-peer")

class Portal {
	constructor(app, roomID, peer) {
		this.app = app

		this.roomID = roomID
		this.peer = peer
	}

	static fromEntry(app, entry) {
		if (entry.type !== "portal") {
			throw new Error("MatrixUser can only be created from entry type \"portal\"")
		}

		return new Portal(app, entry.data.roomID, TelegramPeer.fromSubentry(entry.data.peer))
	}
}
