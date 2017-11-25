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
const { Bridge } = require("matrix-appservice-bridge")
const escapeHTML = require("escape-html")
const sanitizeHTML = require("sanitize-html")
const marked = require("marked")
const commands = require("./commands")
const MatrixUser = require("./matrix-user")
const TelegramUser = require("./telegram-user")
const Portal = require("./portal")

/**
 * The base class for the bridge.
 */
class MautrixTelegram {
	/**
	 * Create a MautrixTelegram instance with the given config data.
	 *
	 * @param config The data from the config file.
	 */
	constructor(config) {
		this.config = config

		this.matrixUsersByID = new Map()
		this.telegramUsersByID = new Map()
		this.portalsByPeerID = new Map()
		this.portalsByRoomID = new Map()

		const self = this
		this.bridge = new Bridge({
			homeserverUrl: config.homeserver.address,
			domain: config.homeserver.domain,
			registration: config.appservice.registration,
			controller: {
				onUserQuery(user) {
					return {}
				},
				async onEvent(request, context) {
					try {
						await self.handleMatrixEvent(request.getData())
					} catch (err) {
						console.error("Matrix event handling failed:", err)
						console.error(err.stack)
					}
				},
			},
		})
	}

	/**
	 * Start the bridge.
	 */
	async run() {
		console.log("Appservice listening on port %s", this.config.appservice.port)
		await this.bridge.run(this.config.appservice.port, {})
		const userEntries = await this.bridge.getUserStore()
			.select({ type: "matrix" })
		for (const entry of userEntries) {
			const user = MatrixUser.fromEntry(this, entry)
			this.matrixUsersByID.set(entry.id, user)
		}
	}

	/**
	 * The {@link MatrixClient} object for the appservice bot.
	 */
	get bot() {
		return this.bridge.getBot()
	}

	/**
	 * The {@link Intent} object for the appservice bot.
	 */
	get botIntent() {
		return this.bridge.getIntent()
	}

	/**
	 * Get the {@link Intent} for the Telegram user with the given ID.
	 *
	 * This does not care if a {@link TelegramUser} object for the user ID exists.
	 * It simply returns an intent for a Matrix puppet user with the correct MXID.
	 *
	 * @param {number} id The ID of the Telegram user.
	 * @returns {Intent} The Matrix puppet intent for the given Telegram user.
	 */
	getIntentForTelegramUser(id) {
		return this.bridge.getIntentFromLocalpart(
			this.config.bridge.username_template.replace("${ID}", id))
	}

	/**
	 * Get a {@link Portal} by Telegram peer.
	 *
	 * This will either get the room from the room cache or the bridge room database.
	 * If the room is not found, a new {@link Portal} object is created.
	 *
	 * @param {TelegramPeer} peer The TelegramPeer object whose portal to get.
	 * @returns {Promise<Portal>} The Portal object.
	 */
	async getPortalByPeer(peer, { createIfNotFound = true } = {}) {
		let portal = this.portalsByPeerID.get(peer.id)
		if (portal) {
			return portal
		}

		const query = {
			type: "portal",
			id: peer.id,
		}
		if (peer.type === "user") {
			query.receiverID = peer.receiverID
		}
		const entries = await this.bridge.getRoomStore()
			.select(query)

		// Handle possible db query race conditions
		portal = this.portalsByPeerID.get(peer.id)
		if (portal) {
			return portal
		}

		if (entries.length) {
			portal = Portal.fromEntry(this, entries[0])
		} else if (createIfNotFound) {
			portal = new Portal(this, undefined, peer)
		} else {
			return undefined
		}
		this.portalsByPeerID.set(peer.id, portal)
		if (portal.roomID) {
			this.portalsByRoomID.set(portal.roomID, portal)
		}
		return portal
	}

	/**
	 * Get a {@link Portal} by Matrix room ID.
	 *
	 * This will either get the room from the room cache or the bridge room database.
	 * If the room is not found, this function WILL NOT create a new room,
	 * but rather just return {@linkplain undefined}.
	 *
	 * @param {string} id The Matrix room ID of the portal to get.
	 * @returns {Promise<Portal>} The Portal object.
	 */
	async getPortalByRoomID(id) {
		let portal = this.portalsByRoomID.get(id)
		if (portal) {
			return portal
		}

		// Check if we have it stored in the by-peer map
		// FIXME this is probably useless
		for (const [_, portalByPeer] of this.portalsByPeerID) {
			if (portalByPeer.roomID === id) {
				this.portalsByRoomID.set(id, portal)
				return portalByPeer
			}
		}

		const entries = await this.bridge.getRoomStore()
			.select({
				type: "portal",
				roomID: id,
			})

		// Handle possible db query race conditions
		portal = this.portalsByRoomID.get(id)
		if (portal) {
			return portal
		}

		if (entries.length) {
			portal = Portal.fromEntry(this, entries[0])
		} else {
			// Don't create portals based on room ID
			return undefined
		}
		this.portalsByPeerID.set(portal.id, portal)
		this.portalsByRoomID.set(id, portal)
		return portal
	}

	/**
	 * Get a {@link TelegramUser} by ID.
	 *
	 * This will either get the user from the user cache or the bridge user database.
	 * If the user is not found, a new {@link TelegramUser} instance is created.
	 *
	 * @param {number} id The internal Telegram ID of the user to get.
	 * @returns {Promise<TelegramUser>} The TelegramUser object.
	 */
	async getTelegramUser(id, { createIfNotFound = true } = {}) {
		let user = this.telegramUsersByID.get(id)
		if (user) {
			return user
		}

		const entries = await this.bridge.getUserStore()
			.select({
				type: "remote",
				id,
			})

		// Handle possible db query race conditions
		if (this.telegramUsersByID.has(id)) {
			return this.telegramUsersByID.get(id)
		}

		if (entries.length) {
			user = TelegramUser.fromEntry(this, entries[0])
		} else if (createIfNotFound) {
			user = new TelegramUser(this, id)
		} else {
			return undefined
		}
		this.telegramUsersByID.set(id, user)
		return user
	}

	/**
	 * Get a {@link MatrixUser} by ID.
	 *
	 * This will either get the user from the user cache or the bridge user database.
	 * If the user is not found, a new {@link MatrixUser} instance is created.
	 *
	 * @param {string} id The MXID of the Matrix user to get.
	 * @returns {Promise<MatrixUser>} The MatrixUser object.
	 */
	async getMatrixUser(id, { createIfNotFound = true } = {}) {
		let user = this.matrixUsersByID.get(id)
		if (user) {
			return user
		}

		const entries = this.bridge.getUserStore()
			.select({
				type: "matrix",
				id,
			})

		// Handle possible db query race conditions
		if (this.matrixUsersByID.has(id)) {
			return this.matrixUsersByID.get(id)
		}

		if (entries.length) {
			user = MatrixUser.fromEntry(this, entries[0])
		} else if (createIfNotFound) {
			user = new MatrixUser(this, id)
		} else {
			return undefined
		}
		this.matrixUsersByID.set(id, user)
		return user
	}

	/**
	 * Save a user to the bridge user database.
	 *
	 * @param {MatrixUser|TelegramUser} user The user object to save.
	 */
	putUser(user) {
		const entry = user.toEntry()
		return this.bridge.getUserStore()
			.upsert({
				type: entry.type,
				id: entry.id,
			}, entry)
	}

	/**
	 * Save a room to the bridge room database.
	 *
	 * @param {Room} room The Room object to save.
	 */
	putRoom(room) {
		const entry = room.toEntry()
		return this.bridge.getRoomStore()
			.upsert({
				type: entry.type,
				id: entry.id,
			}, entry)
	}

	/**
	 * Handle a single received Matrix event.
	 *
	 * @param evt The Matrix event that occurred.
	 */
	async handleMatrixEvent(evt) {
		const asBotID = this.bridge.getBot()
			.getUserId()
		if (evt.type === "m.room.member" && evt.state_key === asBotID) {
			if (evt.content.membership === "invite") {
				// Accept all invites
				this.botIntent.join(evt.room_id)
					.catch(err => {
						console.warn(`Failed to join room ${evt.room_id}:`, err)
						if (err instanceof Error) {
							console.warn(err.stack)
						}
					})
			}
			return
		}

		if (evt.sender === asBotID || evt.type !== "m.room.message" || !evt.content) {
			// Ignore own messages and non-message events.
			return
		}

		const user = await this.getMatrixUser(evt.sender)

		const cmdprefix = this.config.bridge.commands.prefix
		if (evt.content.body.startsWith(`${cmdprefix} `)) {
			if (!user.whitelisted) {
				this.botIntent.sendText(evt.room_id, "You are not authorized to use this bridge.")
				return
			}

			const prefixLength = cmdprefix.length + 1
			const args = evt.content.body.substr(prefixLength)
				.split(" ")
			const command = args.shift()
			commands.run(user, command, args,
				(reply, { allowHTML = false, markdown = true } = {}) => {
					reply = reply.replace("$cmdprefix", cmdprefix)
					if (!allowHTML) {
						reply = escapeHTML(reply)
					}
					if (markdown) {
						reply = marked(reply)
					}
					this.botIntent.sendMessage(
						evt.room_id, {
							body: sanitizeHTML(reply),
							formatted_body: reply,
							msgtype: "m.notice",
							format: "org.matrix.custom.html",
						})
				},
				this)
			return
		}

		if (!user.whitelisted) {
			// Non-management command from non-whitelisted user -> fail silently.
			return
		}

		const portal = await this.getPortalByRoomID(evt.room_id)
		if (portal) {
			portal.handleMatrixEvent(user, evt)
		}
	}

	/**
	 * Check whether the given user ID is allowed to use this bridge.
	 *
	 * @param {string} userID The full Matrix ID to check (@user:homeserver.tld)
	 * @returns {boolean}     Whether or not the user should be allowed to use the bridge.
	 */
	checkWhitelist(userID) {
		if (!this.config.bridge.whitelist || this.config.bridge.whitelist.length === 0) {
			return true
		}

		userID = userID.toLowerCase()
		const userIDCapture = /@.+:(.+)/.exec(userID)
		const homeserver = userIDCapture && userIDCapture.length > 1 ? userIDCapture[1] : undefined
		for (let whitelisted of this.config.bridge.whitelist) {
			whitelisted = whitelisted.toLowerCase()
			if (whitelisted === userID || (homeserver && whitelisted === homeserver)) {
				return true
			}
		}
		return false
	}
}

module.exports = MautrixTelegram
