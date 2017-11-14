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
		this.type = "portal"

		this.roomID = roomID
		this.peer = peer
		this.accessHashes = new Map()
	}

	static fromEntry(app, entry) {
		if (entry.type !== "portal") {
			throw new Error("MatrixUser can only be created from entry type \"portal\"")
		}

		const portal = new Portal(app, entry.data.roomID, TelegramPeer.fromSubentry(entry.data.peer))
		if (portal.peer.type === "channel") {
			portal.accessHashes = new Map(entry.data.accessHashes)
		}
		return portal
	}

	async createMatrixRoom(telegramPOV) {
		if (this.roomID) {
			return
		}

		try {
			await this.peer.getInfo(telegramPOV)
		} catch (err) {
			console.error(err)
			console.error(err.stack)
		}
	}

	updateInfo(telegramPOV, dialog) {
		let changed = false
		if (this.peer.type === "channel") {

		}
		if (telegramPOV && this.accessHashes.get(telegramPOV.userID) !== +dialog.access_hash) {
			this.accessHashes.set(telegramPOV.userID, +dialog.access_hash)
			changed = true
		}
		if (this.title !== dialog.title) {
			this.title = dialog.title
			changed = true
		}
		return changed
	}

	toEntry() {
		return {
			type: this.type,
			id: this.roomID,
			peer: this.peer.toSubentry(),
			accessHashes: this.peer.type === "channel"
				? Array.from(this.accessHashes)
				: undefined,
		}
	}

	save() {
		return this.app.putRoom(this)
	}
}

module.exports = Portal
