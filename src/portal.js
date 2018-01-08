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
const formatter = require("./formatter")
const chalk = require("chalk")

/**
 * Portal represents a portal from a Matrix room to a Telegram chat.
 */
class Portal {
	constructor(app, roomID, peer) {
		this.app = app
		this.type = "portal"

		this.roomID = roomID
		this.peer = peer
		this.accessHashes = new Map()
	}

	/**
	 * Get the peer ID of this portal.
	 *
	 * @returns {number} The ID of the peer of the Telegram side of this portal.
	 */
	get id() {
		return this.peer.id
	}

	/**
	 * Get the receiver ID of this portal. Only applicable for private chat portals.
	 *
	 * @returns {number} The ID of the receiving user of this portal.
	 */
	get receiverID() {
		return this.peer.receiverID
	}

	/**
	 * Convert a database entry into a Portal.
	 *
	 * @param   {MautrixTelegram} app   The app main class instance.
	 * @param   {Object}          entry The database entry.
	 * @returns {Portal}                The loaded Portal.
	 */
	static fromEntry(app, entry) {
		if (entry.type !== "portal") {
			throw new Error("MatrixUser can only be created from entry type \"portal\"")
		}

		const portal = new Portal(app, entry.roomID || entry.data.roomID, TelegramPeer.fromSubentry(entry.data.peer))
		portal.photo = entry.data.photo
		portal.avatarURL = entry.data.avatarURL
		if (portal.peer.type === "channel") {
			portal.accessHashes = new Map(entry.data.accessHashes)
		}
		return portal
	}

	/**
	 * Synchronize the user list of this portal.
	 *
	 * @param {TelegramPuppet} telegramPOV The Telegram account whose point of view the data is/should be fetched from.
	 * @param {UserFull[]}     [users]     The list of {@link https://tjhorner.com/tl-schema/type/UserFull user info}
	 *                                     objects.
	 * @returns {boolean}                  Whether or not syncing was successful. It can only be unsuccessful if the
	 *                                     user list was not provided and an access hash was not found for the given
	 *                                     Telegram user.
	 */
	async syncTelegramUsers(telegramPOV, users) {
		if (!users) {
			if (!await this.loadAccessHash(telegramPOV)) {
				return false
			}
			const data = await this.peer.getInfo(telegramPOV)
			users = data.users
		}
		for (const userData of users) {
			const user = await this.app.getTelegramUser(userData.id)
			// We don't want to update avatars here, as it would likely cause a flood error
			await user.updateInfo(telegramPOV, userData, { updateAvatar: false })
			await user.intent.join(this.roomID)
		}
		return true
	}

	/**
	 * Copy a photo from Telegram to Matrix.
	 *
	 * @param {TelegramPuppet} telegramPOV The Telegram account whose point of view the image should be downloaded from.
	 * @param {TelegramUser}   sender      The user who sent the photo.
	 * @param {Photo}          photo       The Telegram {@link https://tjhorner.com/tl-schema/type/Photo Photo} object.
	 * @returns {Object}                   The uploaded Matrix photo object.
	 */
	async copyTelegramPhoto(telegramPOV, sender, photo) {
		const size = photo.sizes.slice(-1)[0]
		const uploaded = await this.copyTelegramFile(telegramPOV, sender, size.location, photo.id)
		uploaded.info.h = size.h
		uploaded.info.w = size.w
		uploaded.info.size = size.size
		uploaded.info.orientation = 0
		return uploaded
	}


	/**
	 * Copy a file from Telegram to Matrix.
	 *
	 * @param {TelegramPuppet} telegramPOV The Telegram account whose point of view the file should be downloaded from.
	 * @param {TelegramUser}   sender      The user who sent the file.
	 * @param {FileLocation}   location    The Telegram {@link https://tjhorner.com/tl-schema/type/FileLocation
	 *                                     FileLocation}.
	 * @returns {Object}                   The uploaded Matrix file object.
	 */
	async copyTelegramFile(telegramPOV, sender, location, id) {
		id = id || location.id
		const file = await telegramPOV.getFile(location)
		const uploaded = await sender.intent.getClient().uploadContent({
			stream: file.buffer,
			name: `${id}.${file.extension}`,
			type: file.mimetype,
		}, { rawResponse: false })
		uploaded.matrixtype = file.matrixtype
		uploaded.info = {
			mimetype: file.mimetype,
			size: location.size,
		}
		return uploaded
	}

	/**
	 * Update the avatar of this portal to the given photo.
	 *
	 * @param {TelegramPuppet} telegramPOV The Telegram account whose point of view the avatar should be downloaded
	 *                                     from, if necessary.
	 * @param {ChatPhoto}      photo       The Telegram {@link https://tjhorner.com/tl-schema/type/ChatPhoto ChatPhoto}
	 *                                     object.
	 * @returns {boolean}                  Whether or not the photo was updated.
	 */
	async updateAvatar(telegramPOV, photo) {
		if (!photo || this.peer.type === "user") {
			return false
		}

		if (this.photo && this.avatarURL &&
			this.photo.dc_id === photo.dc_id &&
			this.photo.volume_id === photo.volume_id &&
			this.photo.local_id === photo.local_id) {
			return false
		}

		const file = await telegramPOV.getFile(photo)
		const name = `${photo.volume_id}_${photo.local_id}.${file.extension}`

		const uploaded = await this.app.botIntent.getClient().uploadContent({
			stream: file.buffer,
			name,
			type: file.mimetype,
		}, { rawResponse: false })

		this.avatarURL = uploaded.content_uri
		this.photo = {
			dc_id: photo.dc_id,
			volume_id: photo.volume_id,
			local_id: photo.local_id,
		}

		await this.app.botIntent.setRoomAvatar(this.roomID, this.avatarURL)
		return true
	}

	/**
	 * Load the access hash for the given puppet.
	 *
	 * @param {TelegramPuppet} telegramPOV The puppet whose access hash to load.
	 * @returns {boolean}                  As specified by {@link TelegramPeer#loadAccessHash(app, telegramPOV)}.
	 */
	loadAccessHash(telegramPOV) {
		return this.peer.loadAccessHash(this.app, telegramPOV, { portal: this })
	}

	/**
	 * Handle a Telegram typing event.
	 *
	 * @param {Object}         evt        The custom event object.
	 * @param {number}         evt.from   The ID of the Telegram user who is typing.
	 * @param {TelegramPeer}   evt.to     The peer where the user is typing.
	 * @param {TelegramPuppet} evt.source The source where this event was captured.
	 */
	async handleTelegramTyping(evt) {
		if (!this.isMatrixRoomCreated()) {
			return
		}
		const typer = await this.app.getTelegramUser(evt.from)
		// The Intent API currently doesn't allow you to set the
		// typing timeout. Once it does, we should set it to ~5.5s
		// as Telegram resends typing notifications every 5 seconds.
		typer.intent.sendTyping(this.roomID, true/*, 5500*/)
	}

	/**
	 * Handle a Telegram service message event.
	 *
	 * @param {Object}         evt        The custom event object.
	 * @param {number}         evt.from   The ID of the Telegram user who caused the service message.
	 * @param {TelegramPeer}   evt.to     The peer to which the message was sent.
	 * @param {TelegramPuppet} evt.source The source where this event was captured.
	 * @param {MessageAction}  evt.action The Telegram {@link https://tjhorner.com/tl-schema/type/MessageAction
	 *                                    MessageAction} object.
	 */
	async handleTelegramServiceMessage(evt) {
		if (!this.isMatrixRoomCreated()) {
			if (evt.action._ === "messageActionChatDeleteUser") {
				// We don't care about user deletions on chats without portals
				return
			}
			this.app.debug("magenta", "Service message received, creating room for", evt.to.id)
			await this.createMatrixRoom(evt.source, { invite: [evt.source.matrixUser.userID] })
			return
		}
		let matrixUser, telegramUser
		switch (evt.action._) {
		case "messageActionChatCreate":
			// Portal gets created at beginning if it doesn't exist
			// Falls through to invite everyone in initial user list
		case "messageActionChatAddUser":
			for (const userID of evt.action.users) {
				matrixUser = await this.app.getMatrixUserByTelegramID(userID)
				if (matrixUser) {
					matrixUser.join(this)
					this.inviteMatrix(matrixUser.userID)
				}
				telegramUser = await this.app.getTelegramUser(userID)
				telegramUser.intent.join(this.roomID)
			}
			break
		case "messageActionChannelCreate":
			// Portal gets created at beginning if it doesn't exist
			// Channels don't send initial user lists 3:<
			break
		case "messageActionChatMigrateTo":
			this.peer.id = evt.action.channel_id
			this.peer.type = "channel"
			const accessHash = await this.peer.fetchAccessHashFromServer(evt.source)
			if (!accessHash) {
				console.error("Failed to fetch access hash for mirgrated channel!")
				break
			}
			this.accessHashes.set(evt.source.userID, accessHash)
			await this.save()
			const sender = await this.app.getTelegramUser(evt.from)
			await sender.sendEmote(this.roomID, "upgraded this group to a supergroup.")
			break
		case "messageActionChatDeleteUser":
			matrixUser = await this.app.getMatrixUserByTelegramID(evt.action.user_id)
			if (matrixUser) {
				matrixUser.leave(this)
				this.kickMatrix(matrixUser.userID, "Left Telegram chat")
			}
			telegramUser = await this.app.getTelegramUser(evt.action.user_id)
			telegramUser.intent.leave(this.roomID)
			break
		case "messageActionChatEditPhoto":
			const sizes = evt.action.photo.sizes
			let largestSize = sizes[0]
			let largestSizePixels = largestSize.w * largestSize.h
			for (const size of sizes) {
				const pixels = size.w * size.h
				if (pixels > largestSizePixels) {
					largestSizePixels = pixels
					largestSize = size
				}
			}
			// TODO once permissions are synced, make the avatar change event come from the user who changed the avatar
			await this.updateAvatar(evt.source, largestSize.location)
			break
		case "messageActionChatEditTitle":
			this.peer.title = evt.action.title
			await this.save()
			const intent = await this.getMainIntent()
			await intent.setRoomName(this.roomID, this.peer.title)
			break
		default:
			this.app.warn("Unhandled service message of type", evt.action._)
			this.app.warn(JSON.stringify(evt.action, "", "  "))
		}
	}

	/**
	 * Context:  Matrix user X is logged into mautrix-telegram and has a private chat portal room with Telegram user Y.
	 *           X sends message to Y from another Telegram client.
	 *
	 * Problem:  We can't control X's Matrix account. We also can't make sure that X's Telegram account's Matrix puppet
	 *           is always in private chat portal rooms, since X could create a private chat portal by inviting Y's
	 *           puppet without giving it, the only AS-controllable user in the room, any power.
	 *
	 * Solution: When encountering an error caused by the above situation, this function is called.
	 *           This function first tries to invite X's Matrix puppet to the room.
	 *           If that fails, text messages are sent through the other user as notices and other messages are dropped.
	 *
	 * @param {Object}       evt    The custom event object (see #handleTelegramMessage(evt))
	 * @param {TelegramUser} sender The Telegram user object of the sender.
	 * @returns {boolean}           Whether or not the puppet for the sender was successfully invited.
	 */
	async tryFixPrivateChatForOutgoingMessage(evt, sender) {
		try {
			const intent = await this.getMainIntent()
			await intent.invite(this.roomID, sender.mxid)
			return true
		} catch (_) {
			const receiver = await this.app.getTelegramUser(evt.to.id, { createIfNotFound: false })
			if (receiver) {
				if (evt.text) {
					receiver.sendNotice(this.roomID, `[Your message from another client] ${evt.text}`)
				}
			}
		}
		return false
	}

	/**
	 * Handle a Telegram service message event.
	 *
	 * @param {Object}          evt            The custom event object.
	 * @param {number}          evt.from       The ID of the Telegram user who sent the message.
	 * @param {number}          evt.fwdFrom    The ID of the Telegram user who originally sent the message.
	 * @param {TelegramPeer}    evt.to         The peer to which the message was sent.
	 * @param {TelegramPuppet}  evt.source     The source where this event was captured.
	 * @param {string}          evt.text       The text in the message.
	 * @param {string}          [evt.caption]  The image/file caption.
	 * @param {MessageEntity[]} [evt.entities] The Telegram {@link https://tjhorner.com/tl-schema/type/MessageEntity
	 *                                         formatting entities} in the message.
	 * @param {messageMediaPhoto}    [evt.photo]    The Telegram {@link https://tjhorner.com/tl-schema/constructor/messageMediaPhoto Photo} attached to the message.
	 * @param {messageMediaDocument} [evt.document] The Telegram {@link https://tjhorner.com/tl-schema/constructor/messageMediaDocument Document} attached to the message.
	 * @param {messageMediaGeo}      [evt.geo]      The Telegram {@link https://tjhorner.com/tl-schema/constructor/messageMediaGeo Location} attached to the message.
	 */
	async handleTelegramMessage(evt) {
		const a = Object.assign({}, evt)
		delete a.source
		if (!this.isMatrixRoomCreated()) {
			try {
				const result = await this.createMatrixRoom(evt.source, { invite: [evt.source.matrixUser.userID] })
				if (!result.roomID) {
					return
				}
			} catch (err) {
				console.error("Error creating room:", err)
				console.error(err.stack)
				return
			}
		}

		const sender = await this.app.getTelegramUser(evt.from)
		try {
			await sender.intent.sendTyping(this.roomID, false)
		} catch (err) {
			if (evt.to.type === "user") {
				if (!await this.tryFixPrivateChatForOutgoingMessage(evt, sender)) {
					return
				}
				await sender.intent.sendTyping(this.roomID, false)
			} else {
				throw err
			}
		}

		// TODO display forwards (evt.fwdFrom)

		if (evt.text && evt.text.length > 0) {
			if (evt.entities) {
				evt.html = formatter.telegramToMatrix(evt.text, evt.entities, this.app)
				sender.sendHTML(this.roomID, evt.html)
			} else {
				sender.sendText(this.roomID, evt.text)
			}
		}

		if (evt.photo) {
			const photo = await this.copyTelegramPhoto(evt.source, sender, evt.photo)
			photo.name = evt.caption || "Uploaded photo"
			sender.sendFile(this.roomID, photo)
		} else if (evt.document) {
			// TODO handle stickers better
			const file = await this.copyTelegramFile(evt.source, sender, evt.document)
			if (evt.caption) {
				file.name = evt.caption
			} else if (file.matrixtype === "m.audio") {
				file.name = "Uploaded audio"
			} else if (file.matrixtype === "m.video") {
				file.name = "Uploaded video"
			} else {
				file.name = "Uploaded document"
			}
			sender.sendFile(this.roomID, file)
		} else if (evt.geo) {
			sender.sendLocation(this.roomID, evt.geo)
		}
	}

	/**
	 * Handle a Matrix event.
	 *
	 * @param {MatrixUser} sender The user who sent the message.
	 * @param {Object}     evt    The {@link https://matrix.org/docs/spec/client_server/r0.3.0.html#event-structure
	 *                            Matrix event}.
	 */
	async handleMatrixEvent(sender, evt) {
		await this.loadAccessHash(sender.telegramPuppet)
		switch (evt.content.msgtype) {
		case "m.text":
			const { message, entities } = formatter.matrixToTelegram(
					evt.content.formatted_body || evt.content.body,
					evt.content.format === "org.matrix.custom.html",
					this.app)
			await sender.telegramPuppet.sendMessage(this.peer, message, entities)
			break
		case "m.video":
		case "m.audio":
		case "m.file":
			// TODO upload document
			//break
		case "m.image":
			const intent = await this.getMainIntent()
			await intent.sendMessage(this.roomID, {
				msgtype: "m.notice",
				body: "Sending files is not yet supported.",
			})
			break
		case "m.location":
			const [, lat, long] = /geo:([-]?[0-9]+\.[0-9]+)+,([-]?[0-9]+\.[0-9]+)/.exec()
			await sender.telegramPuppet.sendMedia(this.peer, {
				_: "inputMediaGeoPoint",
				geo_point: {
					_: "inputGeoPoint",
					lat: +lat,
					long: +long,
				},
			})
			break
		default:
			this.app.warn("Unhandled event:", JSON.stringify(evt, "", "  "))
		}
	}

	/**
	 * @returns {boolean} Whether or not a Matrix room has been created for this Portal.
	 */
	isMatrixRoomCreated() {
		return !!this.roomID
	}

	/**
	 * Get the primary intent object for this Portal.
	 *
	 * For groups and channels, this is always the AS bot intent.
	 * For private chats, it is the intent of the other user.
	 *
	 * @returns {Intent} The primary intent.
	 */
	async getMainIntent() {
		return this.peer.type === "user"
			? (await this.app.getTelegramUser(this.peer.id)).intent
			: this.app.botIntent
	}

	async inviteTelegram(telegramPOV, user) {
		if (this.peer.type === "chat") {
			const updates = await telegramPOV.client("messages.addChatUser", {
				chat_id: this.peer.id,
				user_id: user.toPeer(telegramPOV).toInputObject(),
				fwd_limit: 50,
			})
			this.app.debug("green", "Chat invite result:", JSON.stringify(updates, "", "  "))
		} else if (this.peer.type === "channel") {
			const updates = await telegramPOV.client("channels.inviteToChannel", {
				channel: this.peer.toInputObject(),
				users: [user.toPeer(telegramPOV).toInputObject()],
			})
			this.app.debug("green", "Channel invite result:", JSON.stringify(updates, "", "  "))
		} else {
			throw new Error(`Can't invite user to peer type ${this.peer.type}`)
		}
	}

	async kickTelegram(telegramPOV, user) {
		let updates
		if (this.peer.type === "chat") {
			updates = await telegramPOV.client("messages.deleteChatUser", {
				chat_id: this.peer.id,
				user_id: user.toPeer(telegramPOV).toInputObject(),
			})
		} else if (this.peer.type === "channel") {
			this.loadAccessHash(telegramPOV)
			updates = await telegramPOV.client("channels.kickFromChannel", {
				channel: this.peer.toInputObject(),
				user_id: user.toPeer(telegramPOV).toInputObject(),
				kicked: true,
			})
		} else {
			throw new Error(`Can't invite user to peer type ${this.peer.type}`)
		}
		await telegramPOV.handleUpdate(updates)
	}

	/**
	 * Invite one or more Matrix users to this Portal.
	 *
	 * @param {string[]|string} users The MXID or list of MXIDs to invite.
	 */
	async inviteMatrix(users) {
		const intent = await this.getMainIntent()
		// TODO check membership before inviting?
		if (Array.isArray(users)) {
			for (const userID of users) {
				if (typeof userID === "string") {
					try {
						await intent.invite(this.roomID, userID)
					} catch (err) {
						if (err.httpStatus !== 403) {
							console.error(`Failed to invite ${userID} to ${this.roomID}:`)
							console.error(err)
						}
					}
				}
			}
		} else if (typeof users === "string") {
			try {
				await intent.invite(this.roomID, users)
			} catch (err) {
				if (err.httpStatus !== 403) {
					console.error(`Failed to invite ${users} to ${this.roomID}:`)
					console.error(err)
				}
			}
		}
	}

	/**
	 * Kick one or more Matrix users from this Portal.
	 *
	 * @param {string[]|string} users  The MXID or list of MXIDs to kick.
	 * @param {string}          reason The reason for kicking the user(s).
	 */
	async kickMatrix(users, reason) {
		const intent = await this.getMainIntent()
		if (Array.isArray(users)) {
			for (const userID of users) {
				if (typeof userID === "string") {
					intent.kick(this.roomID, users, reason)
				}
			}
		} else if (typeof users === "string") {
			intent.kick(this.roomID, users, reason)
		}
	}

	async createTelegramChat(telegramPOV, title) {
		const members = await this.app.getRoomMembers(this.roomID)
		const telegramInviteIDs = []
		const asBotID = this.app.bot.getUserId()
		for (const member of members) {
			if (member === asBotID) {
				continue
			}
			const user = await this.app.getMatrixUser(member)
			if (user._telegramPuppet) {
				telegramInviteIDs.push(user.telegramPuppet.userID)
			}

			const match = this.app.usernameRegex.exec(member)
			if (!match || match.length < 2) {
				continue
			}
			telegramInviteIDs.push(+match[1])
		}
		if (telegramInviteIDs.length < 2) {
			// TODO once we have the option for a bot, this error will need to be changed.
			throw new Error("Not enough users")
		}

		const telegramInvites = []
		for (const userID of telegramInviteIDs) {
			const user = await this.app.getTelegramUser(userID, { createIfNotFound: false })
			if (!user) {
				continue
			}
			telegramInvites.push(user.toPeer(telegramPOV).toInputObject())
		}

		const createUpdates = await telegramPOV.client("messages.createChat", {
			title,
			users: telegramInvites,
		})
		const chat = createUpdates.chats[0]
		this.peer = new TelegramPeer("chat", chat.id, { title })
		await this.save()
	}

	async upgradeTelegramChat(telegramPOV) {
		if (this.peer.type !== "chat") {
			throw new Error("Can't upgrade non-chat portal.")
		}
		const updates = await telegramPOV.client("messages.migrateChat", {
			chat_id: this.id,
		})
		await telegramPOV.handleUpdate(updates)
	}

	/**
	 * Create a Matrix room for this portal.
	 *
	 * @param {TelegramPuppet} telegramPOV
	 * @param {string|string[] invite
	 * @param {boolean}        inviteEvenIfNotCreated
	 * @returns {{created: boolean, roomID: string}}
	 */
	async createMatrixRoom(telegramPOV, { invite = [], inviteEvenIfNotCreated = true } = {}) {
		if (this.roomID) {
			if (invite && inviteEvenIfNotCreated) {
				await this.inviteMatrix(invite)
			}
			return {
				created: false,
				roomID: this.roomID,
			}
		}
		if (this.creatingMatrixRoom) {
			await new Promise(resolve => setTimeout(resolve, 1000))
			return {
				created: false,
				roomID: this.roomID,
			}
		}
		this.creatingMatrixRoom = true

		if (!await this.loadAccessHash(telegramPOV)) {
			this.creatingMatrixRoom = false
			throw new Error(`Failed to load access hash for ${this.peer.type} ${this.peer.username || this.peer.id}.`)
		}

		let room, info, users
		try {
			({ info, users } = await this.peer.getInfo(telegramPOV))
			if (this.peer.type === "chat") {
				room = await this.app.botIntent.createRoom({
					options: {
						name: info.title,
						topic: info.about,
						visibility: "private",
						invite,
					},
				})
			} else if (this.peer.type === "channel") {
				room = await this.app.botIntent.createRoom({
					options: {
						name: info.title,
						topic: info.about,
						visibility: info.username ? "public" : "private",
						room_alias_name: info.username
							? this.app.config.bridge.alias_template.replace("${NAME}", info.username)
							: undefined,
						invite,
					},
				})
			} else if (this.peer.type === "user") {
				const user = await this.app.getTelegramUser(info.id)
				await user.updateInfo(telegramPOV, info, { updateAvatar: true })
				room = await user.intent.createRoom({
					createAsClient: true,
					options: {
						name: this.peer.id === this.peer.receiverID
							? "Saved Messages (Telegram)"
							: undefined, //user.getDisplayName(),
						topic: "Telegram private chat",
						visibility: "private",
						invite,
					},
				})
			} else {
				this.creatingMatrixRoom = false
				throw new Error(`Unrecognized peer type: ${this.peer.type}`)
			}
		} catch (err) {
			this.creatingMatrixRoom = false
			throw err instanceof Error ? err : new Error(err)
		}

		this.roomID = room.room_id
		this.creatingMatrixRoom = false
		this.app.portalsByRoomID.set(this.roomID, this)
		await this.save()
		if (this.peer.type !== "user") {
			try {
				await this.syncTelegramUsers(telegramPOV, users)
				if (info.photo && info.photo.photo_big) {
					await this.updateAvatar(telegramPOV, info.photo.photo_big)
				}
			} catch (err) {
				console.error(err)
				if (err instanceof Error) {
					console.error(err.stack)
				}
			}
		}
		return {
			created: true,
			roomID: this.roomID,
		}
	}

	async updateInfo(telegramPOV, dialog) {
		if (!dialog) {
			this.app.warn("updateInfo called without dialog data")
			const { user } = this.peer.getInfo(telegramPOV)
			if (!user) {
				throw new Error("Dialog data not given and fetching data failed")
			}
			dialog = user
		}
		let changed = false
		if (this.peer.type === "channel") {
			if (telegramPOV && this.accessHashes.get(telegramPOV.userID) !== dialog.access_hash) {
				this.accessHashes.set(telegramPOV.userID, dialog.access_hash)
				changed = true
			}
		}
		if (this.peer.type === "user") {
			const user = await this.app.getTelegramUser(this.peer.id)
			await user.updateInfo(telegramPOV, dialog)
		} else if (dialog.photo && dialog.photo.photo_big) {
			changed = await this.updateAvatar(telegramPOV, dialog.photo.photo_big) || changed
		}
		changed = this.peer.updateInfo(dialog) || changed
		if (changed) {
			this.save()
		}
		return changed
	}

	/**
	 * Convert this Portal into a database entry.
	 *
	 * @returns {Object} A room store database entry.
	 */
	toEntry() {
		return {
			type: this.type,
			id: this.id,
			receiverID: this.receiverID,
			roomID: this.roomID,
			data: {
				peer: this.peer.toSubentry(),
				photo: this.photo,
				avatarURL: this.avatarURL,
				accessHashes: this.peer.type === "channel"
					? Array.from(this.accessHashes)
					: undefined,
			},
		}
	}

	/**
	 * Save this Portal to the database.
	 */
	save() {
		return this.app.putRoom(this)
	}
}

module.exports = Portal
