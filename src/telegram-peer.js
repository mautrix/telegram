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

/**
 * TelegramPeer represents some Telegram entity that can be messaged.
 *
 * The possible peer types are chat (groups), channel (includes supergroups) and user.
 */
class TelegramPeer {
	constructor(type, id, { accessHash, receiverID, username, title } = {}) {
		this.type = type
		this.id = id
		this.accessHash = accessHash
		this.receiverID = receiverID
		this.username = username
		this.title = title
	}

	/**
	 * Create a TelegramPeer based on peer data received from Telegram.
	 *
	 * @param {Object} peer       The data received from Telegram.
	 * @param {number} sender     The user ID of the other person, in case the peer is an user referring to the receiver.
	 * @param {number} receiverID The user ID of the receiver (in case peer type is {@code user})
	 * @returns {TelegramPeer}
	 */
	static fromTelegramData(peer, sender, receiverID) {
		switch (peer._) {
		case "peerChat":
			return new TelegramPeer("chat", peer.chat_id)
		case "peerUser":
			return new TelegramPeer("user", sender, {
				accessHash: peer.access_hash,
				receiverID,
			})
		case "peerChannel":
			return new TelegramPeer("channel", peer.channel_id, {
				accessHash: peer.access_hash,
			})
		default:
			throw new Error(`Unrecognized peer type ${peer._}`)
		}
	}

	/**
	 * Load the access hash for a specific puppeted Telegram user from the channel portal or TelegramUser info.
	 *
	 * @param {MautrixTelegram} app         The app main class instance.
	 * @param {TelegramPuppet}  telegramPOV The puppeted Telegram user for whom the access hash is needed.
	 * @param {Portal}          [portal]    Optional channel {@link Portal} instance to avoid calling {@link app#getPortalByPeer(peer)}.
	 *                                      Only used if {@link #type} is {@code user}.
	 * @param {TelegramUser}    [user]      Optional {@link TelegramUser} instance to avoid calling {@link app#getTelegramUser(id)}.
	 *                                      Only used if {@link #type} is {@code channel}.
	 * @returns {boolean}                   Whether or not the access hash was found and loaded.
	 */
	async loadAccessHash(app, telegramPOV, { portal, user } = {}) {
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
		return false
	}

	/**
	 * Update info based on a Telegram dialog.
	 *
	 * @param             dialog The dialog data sent by Telegram.
	 * @returns {boolean}        Whether or not something was changed.
	 */
	async updateInfo(dialog) {
		let changed = false
		if (dialog.username && (this.type === "channel" || this.type === "user")) {
			if (this.username !== dialog.username) {
				this.username = dialog.username
				changed = true
			}
		}
		if (dialog.title && this.title !== dialog.title) {
			this.title = dialog.title
			changed = true
		}
		return changed
	}

	async fetchAccessHashFromServer(telegramPOV) {
		const data = await this.getInfoFromDialogs(telegramPOV)
		if (!data) {
			return undefined
		}
		this.accessHash = data.access_hash
		return this.accessHash
	}

	async getInfoFromDialogs(telegramPOV) {
		const dialogs = await telegramPOV.client("messages.getDialogs", {})
		if (this.type === "user") {
			for (const user of dialogs.users) {
				if (user.id === this.id) {
					return user
				}
			}
		} else {
			for (const chat of dialogs.chats) {
				if (chat.id === this.id) {
					return chat
				}
			}
		}
		return undefined
	}

	/**
	 * Get info about this peer from the Telegram servers.
	 *
	 * @param   {TelegramPuppet} telegramPOV           The Telegram user whose point of view the data should be fetched from.
	 * @returns {{info: Object, users: Array<Object>}} The info sent by Telegram. For user-type peers, the users array
	 *                                                 is unnecessary.
	 */
	async getInfo(telegramPOV) {
		let info, users
		switch (this.type) {
		case "user":
			info = await telegramPOV.client("users.getFullUser", {
				id: this.toInputObject(),
			})
			users = [info.user]
			info = info.user
			break
		case "chat":
			info = await telegramPOV.client("messages.getFullChat", {
				chat_id: this.id,
			})
			users = info.users
			info = info.chats[0]
			break
		case "channel":
			info = await telegramPOV.client("channels.getFullChannel", {
				channel: this.toInputObject(),
			})
			info = info.chats[0]
			try {
				const participants = await telegramPOV.client("channels.getParticipants", {
					channel: this.toInputObject(),
					filter: { _: "channelParticipantsRecent" },
					offset: 0,
					limit: 1000,
				})
				users = participants.users
			} catch (err) {
				// Getting channel participants apparently requires admin.
				// TODO figure out what to do about that ^
				users = []
			}
			break
		default:
			throw new Error(`Unknown peer type ${this.type}`)
		}
		return {
			info,
			users,
		}
	}

	/**
	 * Create a Telegram InputPeer object based on the data in this TelegramPeer.
	 *
	 * @returns {Object} The Telegram InputPeer object.
	 */
	toInputPeer() {
		switch (this.type) {
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

	/**
	 * Create a Telegram input* object (i.e. inputUser or inputChannel) based on the data in this TelegramPeer.
	 *
	 * @returns {Object} The Telegram input* object.
	 */
	toInputObject() {
		switch (this.type) {
		case "chat":
			throw new Error(`Unsupported type ${this.type}`)
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

	/**
	 * Load the data in a database subentry to a new TelegramPeer object.
	 *
	 * @param   {Object}       entry The database subentry.
	 * @returns {TelegramPeer}       The created TelegramPeer object.
	 */
	static fromSubentry(entry) {
		return new TelegramPeer(entry.type, entry.id, entry)
	}

	/**
	 * Convert this TelegramPeer into a subentry that can be stored in the database.
	 *
	 * @returns {Object} The database-storable subentry.
	 */
	toSubentry() {
		return {
			type: this.type,
			id: this.id,
			username: this.username,
			title: this.title,
			receiverID: this.receiverID,
		}
	}
}

module.exports = TelegramPeer
