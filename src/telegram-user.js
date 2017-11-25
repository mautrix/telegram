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
		return new TelegramPeer("user", this.id, {
			accessHash: this.accessHashes.get(telegramPOV.userID),
			receiverID: telegramPOV.userID,
		})
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

	async updateInfo(telegramPOV, user, { updateAvatar = false } = {}) {
		let changed = false
		if (user.first_name && this.firstName !== user.first_name) {
			this.firstName = user.first_name
			changed = true
		}
		if (user.last_name && this.lastName !== user.last_name) {
			this.lastName = user.last_name
			changed = true
		}
		if (user.username && this.username !== user.username) {
			this.username = user.username
			changed = true
		}
		if (user.access_hash && telegramPOV && this.accessHashes.get(telegramPOV.userID) !== user.access_hash) {
			this.accessHashes.set(telegramPOV.userID, user.access_hash)
			changed = true
		}

		const userInfo = await this.intent.getProfileInfo(this.mxid, "displayname")
		if (userInfo.displayname !== this.getDisplayName()) {
			this.intent.setDisplayName(this.app.config.bridge.displayname_template
				.replace("${DISPLAYNAME}", this.getDisplayName()))
		}
		if (updateAvatar && this.updateAvatar(telegramPOV, user)) {
			changed = true
		}

		if (changed) {
			this.save()
		}
		return changed
	}

	get intent() {
		if (!this._intent) {
			this._intent = this.app.getIntentForTelegramUser(this.id)
		}
		return this._intent
	}

	get mxid() {
		return this.intent.client.credentials.userId
	}

	getFirstAndLastName() {
		return [this.firstName, this.lastName].filter(s => !!s).join(" ")
	}

	getDisplayName() {
		if (this.firstName || this.lastName) {
			return this.getFirstAndLastName()
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

	async updateAvatar(telegramPOV, user) {
		if (!user.photo) {
			return false
		}

		const photo = user.photo.photo_big
		if (this.photo && this.avatarURL &&
			this.photo.dc_id === photo.dc_id &&
			this.photo.volume_id === photo.volume_id &&
			this.photo.local_id === photo.local_id) {
			return false
		}

		const file = await telegramPOV.getFile(photo)
		const name = `${photo.volume_id}_${photo.local_id}.${file.extension}`

		const uploaded = await this.uploadContent({
			stream: Buffer.from(file.bytes),
			name,
			type: file.mimetype,
		})

		this.avatarURL = uploaded.content_uri
		this.photo = {
			dc_id: photo.dc_id,
			volume_id: photo.volume_id,
			local_id: photo.local_id,
		}

		await this.intent.setAvatarUrl(this.avatarURL)
		return true
	}
}

module.exports = TelegramUser
