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
const TelegramPuppet = require("./telegram-puppet")

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
		this._telegramPuppet = undefined
	}

	static fromEntry(app, entry) {
		if (entry.type !== "matrix") {
			throw new Error("MatrixUser can only be created from entry type \"matrix\"")
		}

		const user = new MatrixUser(app, entry.id)
		user.phoneNumber = entry.data.phoneNumber
		user.phoneCodeHash = entry.data.phoneCodeHash
		if (entry.data.puppet) {
			user.puppetData = entry.data.puppet
			user.telegramPuppet
		}
		return user
	}

	toEntry() {
		if (this._telegramPuppet) {
			this.puppetData = this.telegramPuppet.toSubentry()
		}
		return {
			type: "matrix",
			id: this.userID,
			data: {
				phoneNumber: this.phoneNumber,
				phoneCodeHash: this.phoneCodeHash,
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

	async sendTelegramCode(phoneNumber) {
		// TODO handle existing login?
		try {
			const result = await this.telegramPuppet.sendCode(phoneNumber)
			this.phoneNumber = phoneNumber
			this.phoneCodeHash = result.phone_code_hash
			await this.saveChanges()
			return result
		} catch (err) {
			return this.parseTelegramError(err)
		}
	}

	async signInToTelegram(phoneCode) {
		if (!this.phoneNumber) throw new Error("Phone number not set")
		if (!this.phoneCodeHash) throw new Error("Phone code not sent")

		const result = await this.telegramPuppet.signIn(this.phoneNumber, this.phoneCodeHash, phoneCode)
		this.phoneCodeHash = undefined
		await this.saveChanges()
		return result
	}

	async checkPassword(password_hash) {
		const result = await this.telegramPuppet.checkPassword(password_hash)
		await this.saveChanges()
		return result
	}

	saveChanges() {
		return this.app.putUser(this)
	}
}

module.exports = MatrixUser
