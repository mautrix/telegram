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

/**
 * TelegramUser represents a Telegram user who probably has an
 * appservice-managed Matrix account.
 */
class TelegramUser {
	constructor(app, id, user) {
		this.app = app
		this.id = id
		this.accessHashes = new Map()
		this._intent = undefined
		if (user) {
			this.updateInfo(undefined, user)
		}
	}

	static fromEntry(app, entry) {
		if (entry.type !== "remote") {
			throw new Error("TelegramUser can only be created from entry type \"remote\"")
		}

		const user = new TelegramUser(app, entry.id)
		const data = entry.data
		user.firstName = data.firstName
		user.lastName = data.lastName
		user.username = data.username
		user.phoneNumber = data.phoneNumber
		user.photo = data.photo
		user.avatarURL = data.avatarURL
		user.accessHashes = new Map(data.accessHashes)
		return user
	}

	toPeer(telegramPOV) {
		return new TelegramPeer("user", this.id, this.accessHashes.get(telegramPOV.userID))
	}

	toEntry() {
		return {
			type: "remote",
			id: this.id,
			data: {
				firstName: this.firstName,
				lastName: this.lastName,
				username: this.username,
				phoneNumber: this.phoneNumber,
				photo: this.photo,
				avatarURL: this.avatarURL,
				accessHashes: Array.from(this.accessHashes),
			},
		}
	}

	updateInfo(telegramPOV, user) {
		let changed = false
		if (telegramPOV && this.accessHashes.get(telegramPOV.userID) !== +user.access_hash) {
			this.accessHashes.set(telegramPOV.userID, +user.access_hash)
			changed = true
		}
		if (this.firstName !== user.first_name) {
			this.firstName = user.first_name
			changed = true
		}
		if (this.lastName !== user.last_name) {
			this.lastName = user.last_name
			changed = true
		}
		if (this.username !== user.username) {
			this.username = user.username
			changed = true
		}
		return changed
	}

	get intent() {
		if (!this._intent) {
			this._intent = this.app.getIntentForTelegramID(this.id)
		}
		return this._intent
	}

	get mxid() {
		return this.intent.client.credentials.userId
	}

	getDisplayName() {
		if (this.firstName || this.lastName) {
			return [this.firstName, this.lastName].filter(s => !!s)
				.join(" ")
		} else if (this.username) {
			return this.username
		} else if (this.phoneNumber) {
			return this.phoneNumber
		}
		return this.id
	}

	save() {
		return this.app.putUser(this)
	}

	sendText(roomID, text) {
		return this.intent.sendText(roomID, text)
	}

	sendImage(roomID, opts) {
		return this.intent.sendMessage(roomID, {
			msgtype: "m.image",
			url: opts.content_uri,
			body: opts.name,
			info: opts.info,
		})
	}

	sendSelfStateEvent(roomID, type, content) {
		return this.intent.sendStateEvent(roomID, type, this.getMxid(), content)
	}

	uploadContent(opts) {
		return this.intent.getClient()
			.uploadContent({
				stream: opts.stream,
				name: opts.name,
				type: opts.type,
			}, {
				rawResponse: false,
			})
	}

	async updateAvatarImageFrom(telegramPOV, user) {
		if (!user.photo) {
			return
		}

		const photo = user.photo.photo_big
		if (this.photo && this.avatarURL &&
			this.photo.dc_id === photo.dc_id &&
			this.photo.volume_id === photo.volume_id &&
			this.photo.local_id === photo.local_id) {
			return this.avatarURL
		}

		const file = await telegramPOV.getFile(photo)
		const name = `${photo.volume_id}_${photo.local_id}.${file.extension}`

		const uploaded = await this.uploadContent({
			stream: new Buffer(file.bytes),
			name: name,
			type: file.mimetype,
		})

		this.avatarURL = response.content_uri
		this.photo = {
			dc_id: photo.dc_id,
			volume_id: photo.volume_id,
			local_id: photo.local_id,
		}

		await this.app.putUser(this)
		return this.avatarURL
	}
}

module.exports = TelegramUser
