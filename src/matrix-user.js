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
		this.contacts = []
		this._telegramPuppet = undefined
	}

	static fromEntry(app, entry) {
		if (entry.type !== "matrix") {
			throw new Error("MatrixUser can only be created from entry type \"matrix\"")
		}

		const user = new MatrixUser(app, entry.id)
		user.phoneNumber = entry.data.phoneNumber
		user.phoneCodeHash = entry.data.phoneCodeHash
		user.contactIDs = entry.data.contactIDs
		if (entry.data.puppet) {
			user.puppetData = entry.data.puppet
			// Create the telegram puppet instance
			user.telegramPuppet
		}
		return user
	}

	toEntry() {
		if (this._telegramPuppet) {
			this.puppetData = this._telegramPuppet.toSubentry()
		}
		return {
			type: "matrix",
			id: this.userID,
			data: {
				phoneNumber: this.phoneNumber,
				phoneCodeHash: this.phoneCodeHash,
				contactIDs: this.contactIDs,
				puppet: this.puppetData,
			},
		}
	}

	get telegramPuppet() {
		if (!this._telegramPuppet) {
			this._telegramPuppet = TelegramPuppet.fromSubentry(this.app, this, this.puppetData || {})
		}
		return this._telegramPuppet
	}

	parseTelegramError(err) {
		const message = err.toPrintable ? err.toPrintable() : err.toString()

		if (err instanceof Error) {
			throw err
		}
		throw new Error(message)
	}

	get contactIDs() {
		return this.contacts.map(contact => contact.id)
	}

	set contactIDs(list) {
		// FIXME This is somewhat dangerous
		setTimeout(async () => {
			if (list) {
				this.contacts = await Promise.all(list.map(id => this.app.getTelegramUser(id)))
			}
		}, 0)
	}

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

	async syncDialogs({ createRooms = true } = {}) {
		const dialogs = await this.telegramPuppet.client("messages.getDialogs", {})
		let changed = false
		for (const dialog of dialogs.chats) {
			if (dialog._ === "chatForbidden" || dialog.deactivated) {
				continue
			}
			const peer = new TelegramPeer(dialog._, dialog.id)
			const portal = await this.app.getPortalByPeer(peer)
			if (await portal.updateInfo(this.telegramPuppet, dialog)) {
				changed = true
			}
			if (createRooms) {
				try {
					const { roomID, created } = await portal.createMatrixRoom(this.telegramPuppet, {
						invite: [this.userID],
					})
					if (!created) {
						// Make sure the user is invited, since the room already exists.

						const intent = this.app.botIntent
						// FIXME check membership before re-inviting
						//const membership = intent.getClient().getRoom(roomID).getMember(this.userID).membership
						//if (membership !== "join") {
						try {
							await intent.invite(roomID, this.userID)
						} catch (_) {
						}
						//}
					}
				} catch (err) {
					console.error(err)
					console.error(err.stack)
				}
			}
		}
		return changed
	}

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
		try {
			const result = await this.telegramPuppet.sendCode(phoneNumber)
			this.phoneNumber = phoneNumber
			this.phoneCodeHash = result.phone_code_hash
			await this.save()
			return result
		} catch (err) {
			return this.parseTelegramError(err)
		}
	}

	async logOutFromTelegram() {
		const ok = await this.telegramPuppet.logOut()
		if (!ok) {
			return false
		}
		this._telegramPuppet = undefined
		this.puppetData = undefined
		await this.save()
		return true
	}

	async signInToTelegram(phoneCode) {
		if (!this.phoneNumber) throw new Error("Phone number not set")
		if (!this.phoneCodeHash) throw new Error("Phone code not sent")

		const result = await this.telegramPuppet.signIn(this.phoneNumber, this.phoneCodeHash, phoneCode)
		this.phoneCodeHash = undefined
		await this.save()
		return result
	}

	async checkPassword(password_hash) {
		const result = await this.telegramPuppet.checkPassword(password_hash)
		await this.save()
		return result
	}

	save() {
		return this.app.putUser(this)
	}
}

module.exports = MatrixUser
