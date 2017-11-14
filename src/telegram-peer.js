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
	constructor(type, id, accessHash) {
		this.type = type
		this.id = id
		this.accessHash = +(accessHash || 0)
	}

	static fromTelegramData(peer) {
		switch(peer._) {
			case "peerChat":
				return new TelegramPeer("chat", peer.chat_id)
			case "peerUser":
				return new TelegramPeer("user", peer.user_id, peer.access_hash || 0)
			case "peerChannel":
				return new TelegramPeer("channel", peer.channel_id, peer.access_hash || 0)
			default:
				throw new Error(`Unrecognized peer type ${peer._}`)
		}
	}

	async getAccessHash(app, telegramPOV) {
		if (this.type === "chat" || this.accessHash > 0) {
			return true
		} else if (this.type === "user") {
			const user = await app.getTelegramUser(this.id)
			if (user.accessHashes.has(telegramPOV.userID)) {
				this.accessHash = user.accessHashes.get(telegramPOV.userID)
				return true
			}
			return false
		} else if (this.type === "channel") {
			const portal = await app.getPortalByPeer(this)
			if (portal.accessHashes.has(telegramPOV.userID)) {
				this.accessHash = portal.accessHashes.get(telegramPOV.userID)
				return true
			}
			return false
		}

	}

	async getInfo(telegramPOV) {
		let info, participants
		switch(this.type) {
			case "user":
				throw new Error("Can't get chat info of user")
			case "chat":
				info = await telegramPOV.client("messages.getFullChat", {
					chat_id: this.id,
				})
				break
			case "channel":
				// FIXME I'm broken (Error: CHANNEL_INVALID)
				info = await telegramPOV.client("channels.getFullChannel", {
					channel: this.toInputChannel(),
				})
				participants = await telegramPOV.client("channels.getParticipants", {
					channel: this.toInputChannel(),
					filter: { _: "channelParticipantsRecent" },
					offset: 0,
					limit: 1000,
				})
			break
			default:
				throw new Error(`Unknown peer type ${this.type}`)
		}
		console.log(JSON.stringify(info, "", "  "))
		console.log(JSON.stringify(participants, "", "  "))
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

	toInputChannel() {
		if (this.type !== "channel") {
			throw new Error(`Cannot convert peer of type ${this.type} into an inputChannel`)
		}

		return {
			_: "inputChannel",
			channel_id: this.id,
			access_hash: this.accessHash,
		}
	}

	static fromSubentry(entry) {
		return new TelegramPeer(entry.type, entry.id)
	}

	toSubentry() {
		return {
			type: this.type,
			id: this.id,
		}
	}

	get key() {
		return `${this.type} ${this.id}`
	}
}

module.exports = TelegramPeer
