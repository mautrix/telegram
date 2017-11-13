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
		this.accessHash = accessHash
	}

	static fromTelegramData(peer) {
		switch(peer._) {
			case "peerChat":
				return new Peer("chat", peer.chat_id)
			case "peerUser":
				return new Peer("user", peer.user_id, peer.access_hash)
			case "peerChannel":
				return new Peer("channel", peer.channel_id, peer.access_hash)
			default:
				throw new Error(`Unrecognized peer type ${peer._}`)
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
		const accessHash = entry.accessHash ? new Buffer(entry.accessHash) : undefined
		return new Peer(entry.type, entry.id, accessHash)
	}

	toSubentry() {
		return {
			type: this.type,
			id: this.id,
			accessHash: this.accessHash.toString(),
		}
	}

	get key() {
		return `${this.type} ${this.id}`
	}
}
