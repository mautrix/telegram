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
const telegram = require("telegram-mtproto")
const pkg = require("../package.json")
const TelegramPeer = require("./telegram-peer")

const META_FROM_FILETYPE = {
	"storage.fileGif": {
		mimetype: "image/gif",
		extension: "gif",
	},
	"storage.fileJpeg": {
		mimetype: "image/jpeg",
		extension: "jpeg",
	},
	"storage.filePng": {
		mimetype: "image/png",
		extension: "png",
	},
}

/**
 * TelegramPuppet represents a Telegram account being controlled from Matrix.
 */
class TelegramPuppet {
	constructor(app, { userID, matrixUser, data, api_hash, api_id, server_config, api_config }) {
		this._client = undefined
		this.userID = userID
		this.matrixUser = matrixUser
		this.data = data

		this.app = app

		this.serverConfig = Object.assign({}, server_config)

		this.apiHash = api_hash
		this.apiID = api_id

		this.puppetStorage = {
			get: async (key) => {
				let value = this.data[key]
				// TODO test and (enable or remove)
				if (typeof value === "string" && value.startsWith("b64:")) {
					value = Array.from(Buffer.from(value.substr("b64:".length), "base64"))
				}
				return value
			},
			set: async (key, value) => {
				// TODO test and (enable or remove)
				if (Array.isArray(value)) {
					console.log("Non-buffer array")
					value = `b64:${Buffer.from(value).toString("base64")}`
				} else if (value instanceof Buffer) {
					console.log("Buffer array")
					value = `b64:${value.toString("base64")}`
				}
				console.warn("SET", key, "=", JSON.stringify(value))
				if (this.data[key] === value) {
					return
				}

				this.data[key] = value
				await this.matrixUser.save()
			},
			remove: async (...keys) => {
				console.warn("DEL", JSON.stringify(keys))
				keys.forEach((key) => delete this.data[key])
				await this.matrixUser.save()
			},
			clear: async () => {
				console.warn("CLR")
				this.data = {}
				await this.matrixUser.save()
			},
		}

		this.apiConfig = Object.assign({}, {
			app_version: pkg.version,
			lang_code: "en",
			api_id,
			initConnection: 0x69796de9,
			layer: 57,
			invokeWithLayer: 0xda9b0d0d,
		}, api_config)

		if (this.data.dc && this.data[`dc${this.data.dc}_auth_key`]) {
			this.listen()
		}
	}

	static fromSubentry(app, matrixUser, data) {
		const userID = data.userID
		delete data.userID
		return new TelegramPuppet(app, Object.assign({
			userID,
			matrixUser,
			data,
		}, app.config.telegram))
	}

	toSubentry() {
		return Object.assign({
			userID: this.userID,
		}, this.data)
	}

	get client() {
		if (!this._client) {
			this._client = telegram.MTProto({
				api: this.apiConfig,
				server: this.serverConfig,
				app: { storage: this.puppetStorage },
			})
		}
		return this._client
	}

	async checkPhone(phone_number) {
		try {
			const status = this.client("auth.checkPhone", { phone_number })
			if (status.phone_registered) {
				return "registered"
			}
			return "unregistered"
		} catch (err) {
			if (err.message === "PHONE_NUMBER_INVALID") {
				return "invalid"
			}
			throw err
		}
	}

	sendCode(phone_number) {
		return this.client("auth.sendCode", {
			phone_number,
			current_number: true,
			api_id: this.apiID,
			api_hash: this.apiHash,
		})
	}

	logOut() {
		return this.client("auth.logOut")
	}

	async signIn(phone_number, phone_code_hash, phone_code) {
		try {
			const result = await
				this.client("auth.signIn", {
					phone_number, phone_code, phone_code_hash,
				})
			return this.signInComplete(result)
		} catch (err) {
			if (err.type !== "SESSION_PASSWORD_NEEDED" && err.message !== "SESSION_PASSWORD_NEEDED") {
				console.error("Unknown login error:", JSON.stringify(err, "", "  "))
				throw err
			}
			const password = await
				this.client("account.getPassword", {})
			return {
				status: "need-password",
				hint: password.hint,
				salt: password.current_salt,
			}
		}
	}

	async checkPassword(password_hash) {
		const result = await this.client("auth.checkPassword", { password_hash })
		return this.signInComplete(result)
	}

	getDisplayName() {
		if (this.data.firstName || this.data.lastName) {
			return [this.firstName, this.lastName].filter(s => !!s)
				.join(" ")
		} else if (this.data.username) {
			return this.data.username
		}
		return this.data.phone_number
	}

	signInComplete(data) {
		this.userID = data.user.id
		this.data.username = data.user.username
		this.data.firstName = data.user.first_name
		this.data.lastName = data.user.last_name
		this.data.phoneNumber = data.user.phone_number
		this.matrixUser.save()
		this.listen()
		return {
			status: "ok",
		}
	}

	async sendMessage(peer, message) {
		const result = await this.client("messages.sendMessage", {
			peer: peer.toInputPeer(),
			message,
			random_id: ~~(Math.random() * (1 << 30)),
		})
		return result
	}

	async sendMedia(peer, media) {
		const result = await this.client("messages.sendMedia", {
			peer: peer.toInputPeer(),
			media,
			random_id: ~~(Math.random() * (1 << 30)),
		})
		// TODO use result? (maybe the ID)
		return result
	}

	async handleMessage(message) {
		const portal = await this.app.getPortalByPeer(message.to)
		if (portal.isMatrixRoomCreated()) {
			const sender = await this.app.getTelegramUser(message.from)
			await portal.handleTelegramEvent(sender, message)
		}
	}

	async onUpdate(update) {
		if (!update) {
			console.log("Oh noes! Empty update")
			return
		}
		let peer, portal
		switch (update._) {
		case "updateUserStatus":
			const user = await this.app.getTelegramUser(update.user_id)
			let status
			switch (update.status._) {
			case "userStatusOnline":
				status = "online"
				break
			case "userStatusOffline":
			default:
				status = "offline"
			}

			await user.intent.getClient()
				.setPresence({ presence: status })
			break
		case "updateUserTyping":
			peer = new TelegramPeer("user", update.user_id, { receiverID: this.userID })
			/* falls through */
		case "updateChatUserTyping":
			peer = peer || new TelegramPeer("chat", update.chat_id)
			portal = await this.app.getPortalByPeer(peer)
			if (portal.isMatrixRoomCreated()) {
				const sender = await this.app.getTelegramUser(update.user_id)
				// The Intent API currently doesn't allow you to set the
				// typing timeout. Once it does, we should set it to ~5.5s
				// as Telegram resends typing notifications every 5 seconds.
				await sender.intent.sendTyping(portal.roomID, true/*, 5500*/)
			}
			break
		case "updateShortMessage":
			peer = new TelegramPeer("user", update.user_id, { receiverID: this.userID })
			/* falls through */
		case "updateShortChatMessage":
			peer = peer || new TelegramPeer("chat", update.chat_id)
			await this.handleMessage({
				from: update.user_id,
				to: peer,
				text: update.message,
			})
			break
		case "updateNewChannelMessage":
		case "updateNewMessage":
			// TODO handle other content types
			update = update.message // Message defined at message#90dddc11 in layer 71
			await this.handleMessage({
				from: update.from_id,
				to: TelegramPeer.fromTelegramData(update.to_id, update.from_id, this.userID),
				text: update.message,
			})
			break
		default:
			console.log(`Update of type ${update._} received:\n${JSON.stringify(update, "", "  ")}`)
		}
	}

	handleUpdate(data) {
		try {
			switch (data._) {
			case "updateShort":
				this.onUpdate(data.update)
				break
			case "updates":
				for (const update of data.updates) {
					this.onUpdate(update)
				}
				break
			case "updateShortMessage":
			case "updateShortChatMessage":
				this.onUpdate(data)
				break
			default:
				console.log("Unrecognized update type:", data._)
			}
		} catch (err) {
			console.error("Error handling update:", err)
			console.log(err.stack)
		}
	}

	async listen() {
		this.client.bus.untypedMessage.observe(data => this.handleUpdate(data.message))

		try {
			//console.log("Updating online status...")
			//const statusUpdate = await this.client("account.updateStatus", { offline: false })
			//console.log(statusUpdate)
			console.log("Fetching initial state...")
			const state = await this.client("updates.getState", {})
			console.log("Initial state:", state)
		} catch (err) {
			console.error("Error getting initial state:", err)
		}
		try {
			console.log("Updating contact list...")
			const changed = await this.matrixUser.syncContacts()
			if (!changed) {
				console.log("Contacts were up-to-date")
			} else {
				console.log("Contacts updated")
			}
		} catch (err) {
			console.error("Failed to update contacts:", err)
		}
		try {
			console.log("Updating dialogs...")
			const changed = await this.matrixUser.syncDialogs()
			if (!changed) {
				console.log("Dialogs were up-to-date")
			} else {
				console.log("Dialogs updated")
			}
		} catch (err) {
			console.error("Failed to update dialogs:", err)
		}
		setInterval(async () => {
			try {
				// TODO use state?
				/*const state = */
				this.client("updates.getState", {})
			} catch (err) {
				console.error("Error updating state:", err)
			}
		}, 5000)
	}

	async getFile(location) {
		location = Object.assign({}, location, { _: "inputFileLocation" })
		delete location.dc_id
		const file = await this.client("upload.getFile", {
			location,
			offset: 0,
			limit: 100 * 1024 * 1024,
		})
		const meta = META_FROM_FILETYPE[file.type._]
		if (meta) {
			file.mimetype = meta.mimetype
			file.extension = meta.extension
		}
		return file
	}
}

module.exports = TelegramPuppet
