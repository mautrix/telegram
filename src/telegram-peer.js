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

class TelegramPeer {
	constructor(type, id, accessHash, receiverID) {
		this.type = type
		this.id = id
		this.accessHash = accessHash
		this.receiverID = receiverID
		this.username = undefined
		this.title = undefined
	}

	static fromTelegramData(peer, receiverID) {
		switch(peer._) {
			case "peerChat":
				return new TelegramPeer("chat", peer.chat_id)
			case "peerUser":
				return new TelegramPeer("user", peer.user_id, peer.access_hash, receiverID)
			case "peerChannel":
				return new TelegramPeer("channel", peer.channel_id, peer.access_hash)
			default:
				throw new Error(`Unrecognized peer type ${peer._}`)
		}
	}

	/**
	 * Load the access hash for a specific puppeted Telegram user from the channel portal or TelegramUser info.
	 *
	 * @param {MautrixTelegram} app         The instance of {@link MautrixTelegram} to use.
	 * @param {TelegramPuppet}  telegramPOV The puppeted Telegram user for whom the access hash is needed.
	 * @param {Portal}          [portal]    Optional channel {@link Portal} instance to avoid calling {@link app#getPortalByPeer(peer)}.
	 *                                      Only used if {@link #type} is {@linkplain user}.
	 * @param {TelegramUser}    [user]      Optional {@link TelegramUser} instance to avoid calling {@link app#getTelegramUser(id)}.
	 *                                      Only used if {@link #type} is {@linkplain channel}.
	 * @returns {Promise<boolean>}          Whether or not the access hash was found and loaded.
	 */
	async loadAccessHash(app, telegramPOV, { portal, user }) {
		if (this.type === "chat") {
			return true
		} else if (this.type === "user") {
			user = user || await app.getTelegramUser(this.id)
			if (user.accessHashes.has(telegramPOV.userID)) {
				this.accessHash = user.accessHashes.get(telegramPOV.userID)
				return true
			}
			return false
		} else if (this.type === "channel") {
			portal = portal || await app.getPortalByPeer(this)
			if (portal.accessHashes.has(telegramPOV.userID)) {
				this.accessHash = portal.accessHashes.get(telegramPOV.userID)
				return true
			}
			return false
		}
	}

	async updateInfo(dialog) {
		let changed = false
		if (this.type === "channel") {
			if (this.username !== dialog.username) {
				this.username = dialog.username
				changed = true
			}
		}
		if (this.title !== dialog.title) {
			this.title = dialog.title
			changed = true
		}
		return changed
	}

	async getInfo(telegramPOV) {
		let info, users
		switch(this.type) {
			case "user":
				info = await telegramPOV.client("users.getFullUser", {
					id: this.toInputObject()
				})
				users = [info.user]
				info = info.user
			case "chat":
				info = await telegramPOV.client("messages.getFullChat", {
					chat_id: this.id,
				})
				users = info.users
				break
			case "channel":
				info = await telegramPOV.client("channels.getFullChannel", {
					channel: this.toInputObject(),
				})
				const participants = await telegramPOV.client("channels.getParticipants", {
					channel: this.toInputObject(),
					filter: { _: "channelParticipantsRecent" },
					offset: 0,
					limit: 1000,
				})
				users = participants.users
				break
			default:
				throw new Error(`Unknown peer type ${this.type}`)
		}
		return {
			info: info.chats[0],
			users
		}
	}

	toInputPeer() {
		switch(this.type) {
			case "chat":
				return {
					_: "inputPeerChat",
					chat_id: this.id,
				}
			case "user":
				return {
					_: "inputPeerUser",
					user_id: this.id,
					access_hash: this.accessHash,
				}
			case "channel":
				return {
					_: "inputPeerChannel",
					channel_id: this.id,
					access_hash: this.accessHash,
				}
			default:
				throw new Error(`Unrecognized peer type ${this.type}`)
		}
	}

	toInputObject() {
		switch(this.type) {
			case "user":
				return {
					_: "inputUser",
					user_id: this.id,
					access_hash: this.accessHash,
				}
			case "channel":
				return {
					_: "inputChannel",
					channel_id: this.id,
					access_hash: this.accessHash,
				}
			default:
				throw new Error(`Unrecognized type ${this.type}`)
		}
	}

	static fromSubentry(entry) {
		const peer = new TelegramPeer(entry.type, entry.id)
		peer.username = entry.username
		peer.title = entry.title
		peer.receiverID = entry.receiverID
		return peer
	}

	toSubentry() {
		return {
			type: this.type,
			id: this.id,
			username: this.username,
			title: this.title,
			receiverID: this.receiverID,
		}
	}

	get key() {
		return `${this.type} ${this.id}`
	}
}

module.exports = TelegramPeer
