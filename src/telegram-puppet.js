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
const { nextRandomInt } = require("telegram-mtproto/lib/bin")
const fileType = require("file-type")
const pkg = require("../package.json")
const TelegramPeer = require("./telegram-peer")
const chalk = require("chalk")

/**
 * @module telegram-puppet
 */

/**
 * Mapping from Telegram file types to MIME types and extensions.
 * @private
 */
function metaFromFileType(type) {
	const extension = type.substr("storage.file".length).toLowerCase()
	let fileClass, mimetype, matrixtype
	switch (type) {
	case "storage.fileGif":
	case "storage.fileJpeg":
	case "storage.filePng":
	case "storage.fileWebp":
		fileClass = "image"
		break
	case "storage.fileMov":
		mimetype = "quicktime"
	case "storage.fileMp4":
		fileClass = "video"
		break
	case "storage.fileMp3":
		mimetype = "mpeg"
		fileClass = "audio"
		break
	case "storage.filePartial":
		throw new Error("Partial files should be completed before fetching their type.")
	case "storage.fileUnknown":
		fileClass = "application"
		mimetype = "octet-stream"
		matrixtype = "m.file"
		break
	default:
		return undefined
	}
	mimetype = `${fileClass}/${mimetype || extension}`
	matrixtype = matrixtype || `m.${fileClass}`
	return { mimetype, extension, matrixtype }
}

/**
 * Mapping from MIME type to Matrix file type. Used when determining MIME type and extension using magic numbers.
 *
 * @param   {string} mime The MIME type.
 * @returns {string}      The corresponding Matrix file type.
 * @private
 */
function matrixFromMime(mime) {
	if (mime.startsWith("audio/")) {
		return "m.audio"
	} else if (mime.startsWith("video/")) {
		return "m.video"
	} else if (mime.startsWith("image/")) {
		return "m.image"
	}
	return "m.file"
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

		this.pts = 0
		this.date = 0
		this.lastID = 0

		this.puppetStorage = {
			get: async (key) => {
				let value = this.data[key]
				if (typeof value === "string" && value.startsWith("b64:")) {
					value = Array.from(Buffer.from(value.substr("b64:".length), "base64"))
				}
				return value
			},
			set: async (key, value) => {
				if (Array.isArray(value)) {
					value = `b64:${Buffer.from(value).toString("base64")}`
				}
				if (this.data[key] === value) {
					return
				}

				this.data[key] = value
				await this.matrixUser.save()
			},
			remove: async (...keys) => {
				keys.forEach((key) => delete this.data[key])
				await this.matrixUser.save()
			},
			clear: async () => {
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
			return [this.data.firstName, this.data.lastName].filter(s => !!s).join(" ")
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

	async sendMessage(peer, message, entities) {
		if (!message) {
			throw new Error("Invalid parameter: message is undefined.")
		}
		const payload = {
			peer: peer.toInputPeer(),
			message,
			entities,
			random_id: [nextRandomInt(0xFFFFFFFF), nextRandomInt(0xFFFFFFFF)],
		}
		if (!payload.entities) {
			// Everything breaks if we send undefined things :/
			delete payload.entities
		}
		const result = await this.client("messages.sendMessage", payload)
		return result
	}

	async sendMedia(peer, media) {
		if (!media) {
			throw new Error("Invalid parameter: media is undefined.")
		}
		const result = await this.client("messages.sendMedia", {
			peer: peer.toInputPeer(),
			media,
			random_id: [nextRandomInt(0xFFFFFFFF), nextRandomInt(0xFFFFFFFF)],
		})
		// TODO use result? (maybe the ID)
		return result
	}

	async onUpdate(update) {
		if (!update) {
			this.app.error("Oh noes! Empty update")
			return
		}
		let to, from, portal
		switch (update._) {
		// Telegram user status handling.
		case "updateUserStatus":
			const user = await this.app.getTelegramUser(update.user_id)
			const presence = update.status._ === "userStatusOnline" ? "online" : "offline"
			await user.intent.getClient().setPresence({ presence })
			return
		//
		// Telegram typing event handling
		//
		case "updateUserTyping":
			to = new TelegramPeer("user", update.user_id, { receiverID: this.userID })
			/* falls through */
		case "updateChatUserTyping":
			to = to || new TelegramPeer("chat", update.chat_id)

			portal = await this.app.getPortalByPeer(to)
			await portal.handleTelegramTyping({
				from: update.user_id,
				to,
				source: this,
			})
			return
		//
		// Telegram message handling/parsing.
		// The actual handling happens after the switch.
		//
		case "updateShortMessage":
			to = new TelegramPeer("user", update.user_id, { receiverID: this.userID })
			from = update.out ? this.userID : update.user_id
			break
		case "updateShortChatMessage":
			to = new TelegramPeer("chat", update.chat_id)
			from = update.from_id
			break
		case "updateNewChannelMessage":
			// TODO use message.post_author
			from = -1
		case "updateNewMessage":
			this.pts = update.pts
			update = update.message // Message defined at message#90dddc11 in layer 71
			from = update.from_id || from
			to = TelegramPeer.fromTelegramData(update.to_id, update.from_id, this.userID)
			break
		case "updateReadMessages":
		case "updateReadHistoryOutbox":
		case "updateReadHistoryInbox":
		case "updateDeleteMessages":
		case "updateRestoreMessages":
			// TODO we probably want to handle those five updates properly
			this.pts = update.pts
			return

		default:
			// Unknown update type
			this.app.warn(`Update of unknown type ${update._} received: ${JSON.stringify(update, "", "  ")}`)
			return
		}
		if (!to) {
			// This shouldn't happen
			this.app.warn("No target found for update", update)
			return
		}
		if (update._ === "messageService" && update.action._ === "messageActionChannelMigrateFrom") {
			return
		}

		portal = await this.app.getPortalByPeer(to)
		if (update._ === "messageService") {
			await portal.handleTelegramServiceMessage({
				from,
				to,
				source: this,
				action: update.action,
			})
			return
		}
		await portal.handleTelegramMessage({
			from,
			to,
			id: update.id,
			date: update.date,
			fwdFrom: update.fwd_from ? update.fwd_from.from_id : 0,
			source: this,
			text: update.message,
			entities: update.entities,
			photo: update.media && update.media._ === "messageMediaPhoto"
				? update.media.photo
				: undefined,
			document: update.media && update.media._ === "messageMediaDocument"
				? update.media.document
				: undefined,
			geo: update.media && update.media._ === "messageMediaGeo"
				? update.media.geo
				: undefined,
			caption: update.media
				? update.media.caption
				: undefined,
		})
	}

	async receiveUsers(users) {
		this.app.debug("green", "Handling received users:", JSON.stringify(users, "", "  "))
		for (const user of users) {
			const telegramUser = await this.app.getTelegramUser(user.id)
			await telegramUser.updateInfo(this, user, true)
		}
	}

	async receiveChats(chats) {
		this.app.debug("green", "Handling received chats:", JSON.stringify(chats, "", "  "))
		for (const chat of chats) {
			const peer = new TelegramPeer(chat._, chat.id, {
				accessHash: chat.access_hash,
			})
			const portal = await this.app.getPortalByPeer(peer)
			await portal.updateInfo(this, chat)
		}
	}

	async handleUpdatesTooLong() {
		this.app.debug("magenta", "Handling updatesTooLong", this.pts, this.date)
		const dat = await this.client("updates.getDifference", {
			pts: this.pts,
			date: this.date,
			qts: -1,
		})
		if (dat._ === "updates.differenceEmpty") {
			this.date = dat.date
			return
		}
		this.app.debug("magenta", `updates.getDifference: ${JSON.stringify(dat, "", "  ")}`)
		// TODO use dat.users and dat.chats
		await this.receiveUsers(dat.users)
		await this.receiveChats(dat.chats)
		this.pts = dat.state.pts
		this.date = dat.state.date
		for (const message of dat.new_messages) {
			await this.onUpdate({
				_: "updateNewMessage",
				pts: this.pts,
				message,
			})
		}
		for (const update of dat.other_updates) {
			await this.onUpdate(update)
		}
	}

	async handleUpdate(data) {
		if (!data.update || data.update._ !== "updateUserStatus") {
			this.app.debug("green", "Raw event for", this.userID, JSON.stringify(data, "", "  "))
		}
		try {
			switch (data._) {
			case "updateShort":
				this.date = data.date
				await this.onUpdate(data.update)
				break
			case "updates":
				this.date = data.date
				await this.receiveUsers(data.users)
				await this.receiveChats(data.chats)
				for (const update of data.updates) {
					await this.onUpdate(update)
				}
				break
			case "updateShortMessage":
			case "updateShortChatMessage":
				await this.onUpdate(data)
				break
			case "updatesTooLong":
				if (this.pts === 0) {
					this.app.warn("updatesTooLong received, but we don't have a persistent timestamp :(")
					break
				}
				await this.handleUpdatesTooLong()
				break
			default:
				this.app.warn("Unrecognized update type:", data._)
			}
		} catch (err) {
			this.app.warn("Error handling update:", err)
		}
	}

	async listen() {
		this.client.bus.untypedMessage.observe(data => this.handleUpdate(data.message))

		try {
			// FIXME updating status crashes or freezes
			//console.log("Updating online status...")
			//const statusUpdate = await this.client("account.updateStatus", { offline: false })
			//console.log(statusUpdate)
			this.app.info("Fetching initial state...")
			const state = await this.client("updates.getState", {})
			this.pts = state.pts
			this.date = state.date
			this.app.debug("green", "Initial state:", JSON.stringify(state, "", "  "))
		} catch (err) {
			console.error("Error getting initial state:", err)
		}
		try {
			this.app.info("Updating contact list...")
			const changed = await this.matrixUser.syncContacts()
			if (!changed) {
				this.app.info("Contacts were up-to-date")
			} else {
				this.app.info("Contacts updated")
			}
		} catch (err) {
			console.error("Failed to update contacts:", err)
		}
		try {
			this.app.info("Updating dialogs...")
			const changed = await this.matrixUser.syncChats()
			if (!changed) {
				this.app.info("Dialogs were up-to-date")
			} else {
				this.app.info("Dialogs updated")
			}
		} catch (err) {
			console.error("Failed to update dialogs:", err)
		}
		setInterval(async () => {
			try {
				await this.client("updates.getState", {})
			} catch (err) {
				console.error("Error updating state:", err)
				console.error(err.stack)
			}
		}, 1000)
	}

	async uploadFile() {

	}

	async getFile(location) {
		if (location.volume_id && location.local_id) {
			location = {
				_: "inputFileLocation",
				volume_id: location.volume_id,
				local_id: location.local_id,
				secret: location.secret,
			}
		} else if (location.id && location.access_hash) {
			location = {
				_: "inputDocumentFileLocation",
				id: location.id,
				access_hash: location.access_hash,
			}
		} else {
			throw new Error("Unrecognized file location type.")
		}
		const file = await this.client("upload.getFile", {
			location,
			offset: 0,
			// Max download size: 100mb
			limit: 100 * 1024 * 1024,
		})
		file.buffer = Buffer.from(file.bytes)
		if (file.type._ === "storage.filePartial") {
			const { mime, ext } = fileType(file.buffer)
			file.mimetype = mime
			file.extension = ext
			file.matrixtype = matrixFromMime(mime)
		} else {
			const meta = metaFromFileType(file.type._)
			if (meta) {
				file.mimetype = meta.mimetype
				file.extension = meta.extension
				file.matrixtype = meta.matrixtype
			}
		}
		return file
	}
}

module.exports = TelegramPuppet
