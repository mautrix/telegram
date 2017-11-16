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

	get id() {
		return this.peer.id
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

	async syncTelegramUsers(telegramPOV, users) {
		if (!users) {
			if (! await this.loadAccessHash(telegramPOV)) {
				return false
			}
			const data = await this.peer.getInfo(telegramPOV)
			users = data.users
		}
		for (const userData of users) {
			const user = await this.app.getTelegramUser(userData.id)
			await user.updateInfo(telegramPOV, userData)
			await user.intent.join(this.roomID)
		}
		return true
	}

	loadAccessHash(telegramPOV) {
		return this.peer.loadAccessHash(this.app, telegramPOV, {portal: this})
	}

	handleMatrixEvent(evt) {
		console.log("Received message from Matrix to portal with room ID", this.roomID)
		console.log(evt)
	}

	async createMatrixRoom(telegramPOV) {
		if (this.roomID) {
			return this.roomID
		}

		try {
			if (! await this.loadAccessHash(telegramPOV)) {
				return undefined
			}

			const {info, users} = await this.peer.getInfo(telegramPOV)

			const room = await this.app.botIntent.createRoom({
				options: {
					name: info.title,
					visibility: "private",
				}
			})

			this.roomID = room.room_id
			this.app.portalsByRoomID.set(this.roomID, this)
			await this.save()

			await this.syncTelegramUsers(telegramPOV, users)

			return this.roomID
		} catch (err) {
			console.error(err)
			console.error(err.stack)
			return undefined
		}
	}

	updateInfo(telegramPOV, dialog) {
		let changed = false
		if (this.peer.type === "channel") {
			if (telegramPOV && this.accessHashes.get(telegramPOV.userID) !== dialog.access_hash) {
				this.accessHashes.set(telegramPOV.userID, dialog.access_hash)
				changed = true
			}
		}
		changed = this.peer.updateInfo(dialog) || changed
		if (changed) {
			this.save()
		}
		return changed
	}

	toEntry() {
		return {
			type: this.type,
			id: this.id,
			data: {
				roomID: this.roomID,
				peer: this.peer.toSubentry(),
				accessHashes: this.peer.type === "channel"
					? Array.from(this.accessHashes)
					: undefined,
			}
		}
	}

	save() {
		return this.app.putRoom(this)
	}
}

module.exports = Portal
