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
const pkg = require("../package.json")
const os = require("os")
const telegram = require("telegram-mtproto")

class TelegramPuppet {
	constructor(opts) {
		this._client = undefined
		this.userID = opts.userID
		this.matrixUser = opts.matrixUser
		this.data = opts.data

		this.app = opts.app

		this.serverConfig = Object.assign({}, opts.server_config)

		this.api_hash = opts.api_hash
		this.api_id = opts.api_id

		this.apiConfig = Object.assign({}, {
			app_version: pkg.version,
			lang_code: "en",
			api_id: opts.api_id,
		}, opts.api_config)
	}

	static fromSubentry(app, matrixUser, data) {
		const userID = data.user_id
		delete data.user_id
		return new TelegramPuppet(Object.assign({
			userID,
			matrixUser,
			data,
			app,
		}, app.config.telegram))
	}

	toSubentry() {
		return Object.assign({
			user_id: this.userID
		}, this.data)
	}

	get datacenter() {
		return { dcID: 1 }
	}

	get client() {
		if (!this._client) {
			this._client = telegram.MTProto({
				api: this.apiConfig,
				server: this.serverConfig,
			})
		}
		return this._client
	}

	sendCode(phone_number) {
		return this.client("auth.sendCode", {
			phone_number,
			current_number: true,
			api_id: this.api_id,
			api_hash: this.api_hash,
		})

	}

	signIn(phone_number, phone_code_hash, phone_code) {
		return this.client("auth.signIn", {
			phone_number, phone_code, phone_code_hash
		})
			.then(
				result => this.signInComplete(result),
				err => {
					if (err.type !== "SESSION_PASSWORD_NEEDED") {
						throw err
					}
					this.client("account.getPassword", {}).then(data => {
						return {
							status: "need-password",
							hint: data.hint,
							salt: data.current_salt
						}
					})
				})
	}

	checkPassword(password_hash) {
		return this.client("auth.checkPassword", {password_hash})
			.then((result) => this.signInComplete(result))
	}

	signInComplete(data) {
		this.userID = data.user.id
		return {
			status: "ok"
		}
	}
}

module.exports = TelegramPuppet
