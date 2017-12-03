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
const TelegramPeer = require("./telegram-peer")
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
		/**
		 * The app config.
		 * @type {Object}
		 */
		this.config = config
		/**
		 * A special-cased {@link TelegramUser} that is used to send broadcasts to a channel.
		 * @type {TelegramUser}
		 */
		this.channelTelegramSender = new TelegramUser(this, -1)

		/**
		 * MXID -> {@link MatrixUser} cache.
		 * @private
		 * @type {Map<string, MatrixUser>}
		 */
		this.matrixUsersByID = new Map()
		/**
		 * Telegram ID -> {@link MatrixUser} cache.
		 * @priavte
		 * @type {Map<number, MatrixUser>}
		 */
		this.matrixUsersByTelegramID = new Map()
		/**
		 * Telegram ID -> {@link TelegramUser} cache.
		 * @private
		 * @type {Map<number, TelegramUser>}
		 */
		this.telegramUsersByID = new Map()
		/**
		 * Telegram peer ID -> {@link Portal} cache.
		 * @private
		 * @type {Map<number, Portal>}
		 */
		this.portalsByPeerID = new Map()
		/**
		 * Matrix room ID -> {@link Portal} cache.
		 * @private
		 * @type {Map<string, Portal>}
		 */
		this.portalsByRoomID = new Map()
		/**
		 * List of management rooms.
		 * @type {string[]}
		 */
		this.managementRooms = []

		/**
		 * A regular expression that matches MXIDs of Telegram user bridged by this bridge.
		 * @type {RegExp}
		 */
		this.usernameRegex = new RegExp(
				`^@${
					this.config.bridge.username_template.replace("${ID}", "([0-9]+)")
				}:${
					this.config.homeserver.domain
				}$`)

		const self = this
		/**
		 * The matrix-appservice-bridge Bridge instance.
		 * @private
		 * @type {Bridge}
		 */
		this.bridge = new Bridge({
			homeserverUrl: config.homeserver.address,
			domain: config.homeserver.domain,
			registration: config.appservice.registration,
			controller: {
				onUserQuery(/*user*/) {
					return {}
				},
				async onEvent(request/*, context*/) {
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

		// Load all Matrix users to cache
		const userEntries = await this.bridge.getUserStore()
			.select({ type: "matrix" })

		for (const entry of userEntries) {
			const user = MatrixUser.fromEntry(this, entry)
			this.matrixUsersByID.set(entry.id, user)
			if (user.telegramUserID) {
				this.matrixUsersByTelegramID.set(user.telegramUserID, user)
			}
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
	 * @param   {number} id The ID of the Telegram user.
	 * @returns {Intent}    The Matrix puppet intent for the given Telegram user.
	 */
	getIntentForTelegramUser(id) {
		if (id === -1) {
			return this.botIntent
		}
		return this.bridge.getIntentFromLocalpart(this.getUsernameForTelegramUser(id))
	}

	/**
	 * Get the Matrix username localpart for the Telegram user with the given ID.
	 *
	 * @param   {number} id The ID of the Telegram user.
	 * @returns {string}    The Matrix username localpart for the given Telegram user.
	 */
	getUsernameForTelegramUser(id) {
		return this.config.bridge.username_template.replace("${ID}", id)
	}

	/**
	 * Get the full Matrix ID ({@code @localpart:homeserver.tld}) for the Telegram user with the given ID.
	 *
	 * @param   {number} id The ID of the Telegram user.
	 * @returns {string}    The full Matrix ID for the given Telegram user.
	 */
	getMXIDForTelegramUser(id) {
		return `@${this.getUsernameForTelegramUser(id)}:${this.config.homeserver.domain}`
	}

	/**
	 * Get the matrix.to link for the Matrix puppet of the Telegram user with the given ID.
	 *
	 * @param   {number} id The ID of the Telegram user.
	 * @returns {string}    A matrix.to link that points to the Matrix puppet of the given user.
	 */
	getMatrixToLinkForTelegramUser(id) {
		return `https://matrix.to/#/${this.getMXIDForTelegramUser(id)}`
	}

	/**
	 * Get a {@link Portal} by Telegram peer or peer ID.
	 *
	 * This will either get the room from the room cache or the bridge room database.
	 * If the room is not found, a new {@link Portal} object is created.
	 *
	 * You may set the {@code opts.createIfNotFound} parameter to change whether or not to create the Portal
	 * automatically. However, if the peer is just the ID, a new room will not be created in any case.
	 *
	 * @param   {TelegramPeer|number} peer      The TelegramPeer object OR the ID of the peer whose portal to get.
	 *                                          If only a peer ID is given, it is assumed that the peer is a chat or a
	 *                                          channel. Searching for user peers requires the receiver ID, thus here it
	 *                                          requires the full TelegramPeer object.
	 * @param   {object}              [opts]    Additional options.
	 * @param   {boolean} opts.createIfNotFound Whether or not to create the room if it is not found
	 * @returns {Portal}                        The Portal object.
	 */
	async getPortalByPeer(peer, { createIfNotFound = true } = {}) {
		if (typeof peer === "number") {
			peer = {
				id: peer,
			}
			createIfNotFound = false
		} else if (!(peer instanceof TelegramPeer)) {
			throw new Error("Invalid argument: peer is not a number or a TelegramPeer.")
		}
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
		const entries = await this.bridge.getRoomStore().select(query)

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
	 * but rather just return {@code undefined}.
	 *
	 * @param   {string} id The Matrix room ID of the portal to get.
	 * @returns {Portal}    The Portal object.
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
				this.portalsByRoomID.set(id, portalByPeer)
				return portalByPeer
			}
		}

		const entries = await this.bridge.getRoomStore().select({
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
	 * @param   {number} id    The internal Telegram ID of the user to get.
	 * @returns {TelegramUser} The TelegramUser object.
	 */
	async getTelegramUser(id, { createIfNotFound = true } = {}) {
		if (id === -1) {
			return this.channelTelegramSender
		}
		// TODO remove this after bugs are fixed
		if (isNaN(parseInt(id, 10))) {
			const err = new Error("Fatal: non-int Telegram user ID")
			console.error(err.stack)
			throw err
		}
		let user = this.telegramUsersByID.get(id)
		if (user) {
			return user
		}

		const entries = await this.bridge.getUserStore().select({
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
	 * Get a {@link MatrixUser} by Telegram user ID.
	 *
	 * This will either get the user from the user cache or the bridge user database.
	 *
	 * @param   {number} id  The Telegram user ID of the Matrix user to get.
	 * @returns {MatrixUser} The MatrixUser object.
	 */
	async getMatrixUserByTelegramID(id) {
		console.log("Searching for Matrix user by Telegram ID", id)
		let user = this.matrixUsersByTelegramID.get(id)
		if (user) {
			console.log("Found in cache", user.userID)
			return user
		}

		// Check if we have the user stored in the by- map
		// FIXME this should be made useless by making sure we always add to the second map when appropriate
		for (const [_, userByMXID] of this.matrixUsersByID) {
			if (userByMXID.telegramUserID === id) {
				console.log("Found in MXID cache", userByMXID.userID)
				this.matrixUsersByTelegramID.set(id, userByMXID)
				return userByMXID
			}
		}

		const entries = this.bridge.getUserStore().select({
			type: "matrix",
			telegramID: id,
		})

		// Handle possible db query race conditions
		if (this.matrixUsersByTelegramID.has(id)) {
			console.log("Found in cache after race", user.userID)
			return this.matrixUsersByTelegramID.get(id)
		}

		if (entries.length) {
			console.log("Found in db", user.userID)
			user = MatrixUser.fromEntry(this, entries[0])
		} else {
			console.log("Not found :(")
			return undefined
		}
		this.matrixUsersByID.set(user.userID, user)
		this.matrixUsersByTelegramID.set(id, user)
		return user
	}

	/**
	 * Get a {@link MatrixUser} by ID.
	 *
	 * This will either get the user from the user cache or the bridge user database.
	 * If the user is not found, a new {@link MatrixUser} instance is created.
	 *
	 * @param   {string} id  The MXID of the Matrix user to get.
	 * @returns {MatrixUser} The MatrixUser object.
	 */
	async getMatrixUser(id, { createIfNotFound = true } = {}) {
		let user = this.matrixUsersByID.get(id)
		if (user) {
			return user
		}

		const entries = this.bridge.getUserStore().select({
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
		if (user.telegramUserID) {
			this.matrixUsersByID.set(user.telegramUserID, user)
		}
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
	 * Get the members in the given room.
	 *
	 * @param   {string} roomID   The ID of the room to search.
	 * @param   {Intent} [intent] The Intent object to use when reading the room state.
	 *                            Uses {@link #botIntent} by default.
	 * @returns {string[]}        The list of MXIDs who are in the room.
	 */
	async getRoomMembers(roomID, intent = this.botIntent) {
		const roomState = await intent.roomState(roomID)
		const members = []
		for (const event of roomState) {
			if (event.type === "m.room.member" && event.membership === "join") {
				members.push(event.user_id)
			}
		}
		return members
	}

	async getRoomTitle(roomID, intent = this.botIntent) {
		const roomState = await intent.roomState(roomID)
		for (const event of roomState) {
			if (event.type === "m.room.name") {
				return event.content.name
			}
		}
		return undefined
	}

	/**
	 * Handle an invite to a Matrix room.
	 *
	 * @param {MatrixUser}  sender The user who sent this invite.
	 * @param {MatrixEvent} evt    The invite event.
	 */
	async handleInvite(sender, evt) {
		const asBotID = this.bridge.getBot().getUserId()
		if (evt.state_key === asBotID) {
			// Accept all AS bot invites.
			try {
				await this.botIntent.join(evt.room_id)
			} catch (err) {
				console.error(`Failed to join room ${evt.room_id}:`, err)
				if (err instanceof Error) {
					console.error(err.stack)
				}
			}
			return
		}
		if (evt.sender === asBotID || evt.sender === evt.state_key) {
			return
		}

		// Check if the invited user is a Telegram user.
		const capture = this.usernameRegex.exec(evt.state_key)
		if (!capture) {
			return
		}

		const telegramID = +capture[1]
		if (!telegramID || isNaN(telegramID)) {
			return
		}

		const intent = this.getIntentForTelegramUser(telegramID)
		try {
			await intent.join(evt.room_id)
			const members = await this.getRoomMembers(evt.room_id, intent)
			const user = await this.getTelegramUser(telegramID)
			if (members.length < 2) {
				console.warn(`No members in room ${evt.room_id}`)
				await intent.leave(evt.room_id)
			} else if (members.length === 2) {
				const peer = user.toPeer(sender.telegramPuppet)
				const portal = await this.getPortalByPeer(peer)
				if (portal.roomID) {
					await intent.sendMessage(evt.room_id, {
						msgtype: "m.notice",
						body: "You already have a private chat room with me!\nI'll re-invite you to that room.",
					})
					try {
						await intent.invite(portal.roomID, sender.userID)
					} catch (_) {}
					await intent.leave(evt.room_id)
				} else {
					portal.roomID = evt.room_id
					await portal.save()
					await intent.sendMessage(portal.roomID, {
						msgtype: "m.notice",
						body: "Portal to Telegram private chat created.",
					})
					await user.updateInfo(sender.telegramPuppet, undefined, { updateAvatar: true })
				}
			} else if (!members.includes(asBotID)) {
				await intent.sendMessage(evt.room_id, {
					msgtype: "m.notice",
					body: "Inviting additional Telegram users to private chats or non-portal rooms is not supported.",
				})
				await intent.leave(evt.room_id)
			} else {
				const portal = await this.getPortalByRoomID(evt.room_id)
				if (portal) {
					await portal.inviteTelegram(sender.telegramPuppet, user)
				}
			}
		} catch (err) {
			console.error(`Failed to process invite to room ${evt.room_id} for Telegram user ${telegramID}: ${err}`)
			if (err instanceof Error) {
				console.error(err.stack)
			}
		}
	}

	/**
	 * Handle a single received Matrix event.
	 *
	 * @param {MatrixEvent} evt The Matrix event that occurred.
	 */
	async handleMatrixEvent(evt) {
		const user = await this.getMatrixUser(evt.sender)
		if (!user.whitelisted) {
			return
		}

		const asBotID = this.bridge.getBot().getUserId()
		if (evt.type === "m.room.member" && evt.content.membership === "invite") {
			await this.handleInvite(user, evt)
			return
		}

		if (evt.sender === asBotID || evt.type !== "m.room.message" || !evt.content) {
			// Ignore own messages and non-message events.
			return
		}

		const cmdprefix = this.config.bridge.commands.prefix
		const hasCommandPrefix = cmdprefix && evt.content.body.startsWith(`${cmdprefix} `)

		const portal = await this.getPortalByRoomID(evt.room_id)
		if (portal && !hasCommandPrefix) {
			portal.handleMatrixEvent(user, evt)
			return
		}

		let isManagement = this.managementRooms.includes(evt.room_id) || hasCommandPrefix
		if (!isManagement) {
			const roomMembers = await this.getRoomMembers(evt.room_id)
			if (roomMembers.length === 2 && roomMembers.includes(asBotID)) {
				this.managementRooms.push(evt.room_id)
				isManagement = true
			}
		}
		if (isManagement) {
			const prefixLength = cmdprefix.length + 1
			if (cmdprefix && evt.content.body.startsWith(`${cmdprefix} `)) {
				evt.content.body = evt.content.body.substr(prefixLength)
			}
			const args = evt.content.body.split(" ")
			const command = args.shift()
			const replyFunc = (reply, { allowHTML = false, markdown = true } = {}) => {
				reply = reply.replace("$cmdprefix", cmdprefix || "")
				if (!markdown && !allowHTML) {
					reply = escapeHTML(reply)
				}
				if (markdown) {
					reply = marked(reply, {
						sanitize: !allowHTML,
					})
				}
				this.botIntent.sendMessage(
						evt.room_id, {
							body: sanitizeHTML(reply),
							formatted_body: reply,
							msgtype: "m.notice",
							format: "org.matrix.custom.html",
						})
			}
			commands.run(user, command, args, replyFunc, {
				app: this,
				evt,
				roomID: evt.room_id,
				isManagement,
				isPortal: !!portal,
			})
		}
	}

	/**
	 * Check whether the given user ID is allowed to use this bridge.
	 *
	 * @param   {string} userID The full Matrix ID to check (@user:homeserver.tld)
	 * @returns {boolean}       Whether or not the user should be allowed to use the bridge.
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
