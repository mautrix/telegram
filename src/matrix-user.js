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
const md5 = require("md5")
const TelegramPuppet = require("./telegram-puppet")
const TelegramPeer = require("./telegram-peer")
const strSim = require("string-similarity")

/**
 * MatrixUser represents a Matrix user who probably wants to control their
 * Telegram account from Matrix.
 */
class MatrixUser {
	constructor(app, userID) {
		this.app = app
		this.userID = userID
		this.whitelisted = app.checkWhitelist(userID)
		this.phoneNumber = undefined
		this.phoneCodeHash = undefined
		this.commandStatus = undefined
		this.puppetData = undefined
		this.contacts = []
		this.chats = []
		this._telegramPuppet = undefined
	}

	/**
	 * Get the user ID of the Telegram user this Matrix user controls.
	 *
	 * @returns {number|undefined} The Telegram user ID, or undefined if not logged in.
	 */
	get telegramUserID() {
		return this._telegramPuppet
			? this._telegramPuppet.userID || undefined
			: undefined
	}

	/**
	 * Convert a database entry into a MatrixUser.
	 *
	 * @param   {MautrixTelegram} app   The app main class instance.
	 * @param   {Object}          entry The database entry.
	 * @returns {MatrixUser}            The loaded MatrixUser.
	 */
	static fromEntry(app, entry) {
		if (entry.type !== "matrix") {
			throw new Error("MatrixUser can only be created from entry type \"matrix\"")
		}

		const user = new MatrixUser(app, entry.id)
		user.phoneNumber = entry.data.phoneNumber
		user.phoneCodeHash = entry.data.phoneCodeHash
		user.setContactIDs(entry.data.contactIDs)
		user.setChatIDs(entry.data.chatIDs)
		if (entry.data.puppet) {
			user.puppetData = entry.data.puppet
			// Create the telegram puppet instance
			user.telegramPuppet
		}
		return user
	}

	/**
	 * Convert this MatrixUser into a database entry.
	 *
	 * @returns {Object} A user store database entry.
	 */
	toEntry() {
		if (this._telegramPuppet) {
			this.puppetData = this._telegramPuppet.toSubentry()
		}
		return {
			type: "matrix",
			id: this.userID,
			telegramID: this.telegramUserID,
			data: {
				phoneNumber: this.phoneNumber,
				phoneCodeHash: this.phoneCodeHash,
				contactIDs: this.contactIDs,
				chatIDs: this.chatIDs,
				puppet: this.puppetData,
			},
		}
	}

	/**
	 * Get the telegram puppet this Matrix user controls.
	 * If one doesn't exist, it'll be created based on the {@link #puppetData} field.
	 *
	 * @returns {TelegramPuppet} The Telegram account controller.
	 */
	get telegramPuppet() {
		if (!this._telegramPuppet) {
			this._telegramPuppet = TelegramPuppet.fromSubentry(this.app, this, this.puppetData || {})
		}
		return this._telegramPuppet
	}

	/**
	 * Get the IDs of all the Telegram contacts of this user.
	 *
	 * @returns {number[]} A list of Telegram user IDs.
	 */
	get contactIDs() {
		return this.contacts.map(contact => contact.id)
	}

	/**
	 * Get the IDs of all the Telegram chats this user is in.
	 *
	 * @returns {number[]} A list of Telegram chat IDs.
	 */
	get chatIDs() {
		return this.chats.map(chat => chat.id)
	}

	/**
	 * Update the contacts of this user based on a list of Telegram user IDs.
	 *
	 * @param {number[]} list The list of Telegram user IDs.
	 */
	async setContactIDs(list) {
		if (!list) {
			return
		}
		this.contacts = await Promise.all(list.map(id => this.app.getTelegramUser(id)))
	}

	/**
	 * Update the chats of this user based on a list of Telegram chat IDs.
	 *
	 * @param {number[]} list The list of Telegram chat IDs.
	 */
	async setChatIDs(list) {
		if (!list) {
			return
		}
		this.chats = await Promise.all(list.map(id => this.app.getPortalByPeer(id)))
	}

	/**
	 * Synchronize the contacts of this user.
	 *
	 * @returns {boolean} Whether or not anything changed.
	 */
	async syncContacts() {
		const contacts = await this.telegramPuppet.client("contacts.getContacts", {
			hash: md5(this.contactIDs.join(",")),
		})
		if (contacts._ === "contacts.contactsNotModified") {
			return false
		}
		for (const [index, contact] of Object.entries(contacts.users)) {
			const telegramUser = await this.app.getTelegramUser(contact.id)
			await telegramUser.updateInfo(this.telegramPuppet, contact, true)
			contacts.users[index] = telegramUser
		}
		this.contacts = contacts.users
		await this.save()
		return true
	}

	/**
	 * Synchronize the chats (groups, channels) of this user.
	 *
	 * @param   {object}  [opts]           Additional options.
	 * @param   {boolean} opts.createRooms Whether or not portal rooms should be automatically created.
	 *                                     Defaults to {@code true}
	 * @returns {boolean} Whether or not anything changed.
	 */
	async syncChats({ createRooms = true } = {}) {
		const dialogs = await this.telegramPuppet.client("messages.getDialogs", {})
		let changed = false

		for (const user of dialogs.users) {
			if (!user.self) {
				continue
			}
			// Automatically create Saved Messages room
			const peer = new TelegramPeer("user", user.id, {
				receiverID: user.id,
				accessHash: user.access_hash,
			})
			const portal = await this.app.getPortalByPeer(peer)
			if (createRooms) {
				try {
					await portal.createMatrixRoom(this.telegramPuppet, {
						invite: [this.userID],
					})
				} catch (err) {
					console.error(err)
					console.error(err.stack)
				}
			}
		}

		this.chats = []
		for (const dialog of dialogs.chats) {
			if (dialog._ === "chatForbidden" || dialog._ === "channelForbidden" || dialog.deactivated) {
				continue
			}
			const peer = new TelegramPeer(dialog._, dialog.id)
			const portal = await this.app.getPortalByPeer(peer)
			if (await portal.updateInfo(this.telegramPuppet, dialog)) {
				changed = true
			}
			this.chats.push(portal)
			if (createRooms) {
				try {
					await portal.createMatrixRoom(this.telegramPuppet, {
						invite: [this.userID],
					})
				} catch (err) {
					console.error(err)
					console.error(err.stack)
				}
			}
		}
		await this.save()
		return changed
	}

	/**
	 * Add a {@link Portal} to the chat list of this user.
	 *
	 * This should only be used for non-private chat portals.
	 *
	 * @param {Portal} portal The portal to add.
	 */
	async join(portal) {
		if (!this.chats.includes(portal.id)) {
			this.chats.push(portal.id)
			await this.save()
		}
	}

	/**
	 * Remove a {@link Portal} from the chat list of this user.
	 *
	 * This should only be used for non-private chat portals.
	 *
	 * @param {Portal} portal The portal to remove.
	 */
	async leave(portal) {
		const chatIDIndex = this.chats.indexOf(portal.id)
		if (chatIDIndex > -1) {
			this.chats.splice(chatIDIndex, 1)
			await this.save()
		}
	}

	/**
	 * Search for contacts of this user.
	 *
	 * @param   {string} query              The search query.
	 * @param   {object} [opts]             Additional options.
	 * @param   {number} opts.maxResults    The maximum number of results to show.
	 * @param   {number} opts.minSimilarity The minimum query similarity, below which results should be ignored.
	 * @returns {Object[]} The search results.
	 */
	async searchContacts(query, { maxResults = 5, minSimilarity = 0.45 } = {}) {
		const results = []
		for (const contact of this.contacts) {
			let displaynameSimilarity = 0
			let usernameSimilarity = 0
			let numberSimilarity = 0
			if (contact.firstName || contact.lastName) {
				displaynameSimilarity = strSim.compareTwoStrings(query, contact.getFirstAndLastName())
			}
			if (contact.username) {
				usernameSimilarity = strSim.compareTwoStrings(query, contact.username)
			}
			if (contact.phoneNumber) {
				numberSimilarity = strSim.compareTwoStrings(query, contact.phoneNumber)
			}
			const similarity = Math.max(displaynameSimilarity, usernameSimilarity, numberSimilarity)
			if (similarity >= minSimilarity) {
				results.push({
					similarity,
					match: Math.round(similarity * 1000) / 10,
					contact,
				})
			}
		}
		return results
			.sort((a, b) => b.similarity - a.similarity)
			.slice(0, maxResults)
	}

	/**
	 * Search for non-contact Telegram users from the point of view of this user.
	 * @param   {string} query           The search query.
	 * @param   {object} [opts]          Additional options.
	 * @param   {number} opts.maxResults The maximum number of results to show.
	 * @returns {Object[]} The search results.
	 */
	async searchTelegram(query, { maxResults = 5 } = {}) {
		const results = await this.telegramPuppet.client("contacts.search", {
			q: query,
			limit: maxResults,
		})
		const resultUsers = []
		for (const userInfo of results.users) {
			const user = await this.app.getTelegramUser(userInfo.id)
			user.updateInfo(this.telegramPuppet, userInfo)
			resultUsers.push(user)
		}
		return resultUsers
	}

	/**
	 * Request a Telegarm phone code for logging in (or registering)
	 *
	 * @param   {string} phoneNumber The phone number.
	 * @returns {Object}             The code send result as returned by {@link TelegramPuppet#sendCode()}.
	 */
	async sendTelegramCode(phoneNumber) {
		if (this._telegramPuppet && this._telegramPuppet.userID) {
			throw new Error("You are already logged in. Please log out before logging in again.")
		}
		switch (this.telegramPuppet.checkPhone(phoneNumber)) {
		case "unregistered":
			throw new Error("That number has not been registered. Please register it first.")
		case "invalid":
			throw new Error("Invalid phone number.")
		}
		const result = await this.telegramPuppet.sendCode(phoneNumber)
		this.phoneNumber = phoneNumber
		this.phoneCodeHash = result.phone_code_hash
		await this.save()
		return result
	}

	/**
	 * Log out from Telegram.
	 */
	async logOutFromTelegram() {
		this.telegramPuppet.logOut()
		// TODO kick user from all portals
		this._telegramPuppet = undefined
		this.puppetData = undefined
		await this.save()
	}

	/**
	 * Sign in to Telegram with a phone code sent using {@link #sendTelegramCode()}.
	 *
	 * @param   {number} phoneCode The phone code.
	 * @returns {Object}           The sign in result as returned by {@link TelegramPuppet#signIn()}.
	 */
	async signInToTelegram(phoneCode) {
		if (!this.phoneNumber) throw new Error("Phone number not set")
		if (!this.phoneCodeHash) throw new Error("Phone code not sent")

		const result = await this.telegramPuppet.signIn(this.phoneNumber, this.phoneCodeHash, phoneCode)
		this.phoneCodeHash = undefined
		await this.save()
		return result
	}

	/**
	 * Finish signing in to Telegram using the two-factor auth password.
	 *
	 * @param   {string} password_hash The salted hash of the password.
	 * @returns {Object}               The sign in result as returned by {@link TelegramPuppet#checkPassword()}
	 */
	async checkPassword(password_hash) {
		const result = await this.telegramPuppet.checkPassword(password_hash)
		await this.save()
		return result
	}

	/**
	 * Save this MatrixUser to the database.
	 */
	save() {
		return this.app.putUser(this)
	}
}

module.exports = MatrixUser
