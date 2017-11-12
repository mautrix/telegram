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
		user.phoneNumber = entry.data.phone_number
		user.phoneCodeHash = entry.data.phone_code_hash
		if (entry.data.puppet) {
			user.puppetData = entry.data.puppet
			user.telegramPuppet
		}
		return user
	}

	toEntry() {
		if (this.puppet) {
			this.puppetData = this.puppet.toSubentry()
		}
		return {
			type: "matrix",
			id: this.userID,
			data: {
				phone_number: this.phoneNumber,
				phone_code_hash: this.phoneCodeHash,
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

	sendTelegramCode(phoneNumber) {
		// TODO handle existing login?

		return this.telegramPuppet.sendCode(phoneNumber)
			.then(result => {
				this.phoneNumber = phoneNumber
				this.phoneCodeHash = result.phone_code_hash
				this.app.putUser(this)
				return result
			}, err => this.parseTelegramError(err))
	}

	signInToTelegram(phoneCode) {
		if (!this.phoneNumber) throw new Error("Phone number not set")
		if (!this.phoneCodeHash) throw new Error("Phone code not sent")

		return this.telegramPuppet.signIn(this.phoneNumber, this.phoneCodeHash, phoneCode)
			.then(result => {
				this.phoneCodeHash = undefined
				return this.app.putUser(this).then(() => result)
			})
	}

	checkPassword(password_hash) {
		return this.telegramPuppet.checkPassword(password_hash)
			.then(() => this.app.putUser(this))
	}
}

module.exports = MatrixUser
