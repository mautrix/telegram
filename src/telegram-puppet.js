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

		this.puppetStorage = {
			get: async (key) => {
				let value = this.data[key]
				/*if (value && key.match(/_auth_key$/)) {
					value = this.app.decrypt(value)
				}*/
				return value
			},
			set: async (key, value) => {
				/*if (value && key.match(/_auth_key$/)) {
					value = this.app.encrypt(value)
				}*/

				if (this.data[key] === value) return Promise.resolve()

				this.data[key] = value
				await this.matrixUser.saveChanges()
			},
			remove: async (...keys) => {
				keys.forEach((key) => delete this.data[key])
				await this.matrixUser.saveChanges()
			},
			clear: async () => {
				this.data = {}
				await this.matrixUser.saveChanges()
			},
		}

		this.apiConfig = Object.assign({}, {
			app_version: pkg.version,
			lang_code: "en",
			api_id: opts.api_id,
			initConnection : 0x69796de9,
			layer: 57,
			invokeWithLayer: 0xda9b0d0d,
		}, opts.api_config)

		if (this.data.dc && this.data[`dc${this.data.dc}_auth_key`]) {
			this.listen()
		}
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
			user_id: this.userID,
		}, this.data)
	}

	get client() {
		if (!this._client) {
			const self = this
			this._client = telegram.MTProto({
				api: this.apiConfig,
				server: this.serverConfig,
				app: { storage: this.puppetStorage },
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

	async signIn(phone_number, phone_code_hash, phone_code) {
		try {
			const result = await
				this.client("auth.signIn", {
					phone_number, phone_code, phone_code_hash,
				})
			this.signInComplete(result)
		} catch (err) {
			if (err.message !== "SESSION_PASSWORD_NEEDED") {
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
		if (this.data.first_name || this.data.last_name) {
			return `${this.data.first_name} ${this.data.last_name}`
		} else if (this.data.username) {
			return this.data.username
		}
		return this.data.phone_number
	}

	signInComplete(data) {
		this.userID = data.user.id
		this.data.username = data.user.username
		this.data.first_name = data.user.first_name
		this.data.last_name = data.user.last_name
		this.data.phone_number = data.user.phone_number
		this.matrixUser.saveChanges()
		this.listen()
		return {
			status: "ok",
		}
	}

	handleUpdate(data) {
		console.log(data)
	}

	async listen() {
		const client = this.client
		client.on("update", data => this.handleUpdate(data))
		if (client.bus) {
			client.bus.untypedMessage.observe(data => this.handleUpdate(data))
		}

		try {
			console.log("Updating online status...")
			//const statusUpdate = await client("account.updateStatus", { offline: false })
			//console.log(statusUpdate)
			console.log("Fetching initial state...")
			const state = await client("updates.getState", {})
			console.log("Initial state:", state)
		} catch (err) {
			console.error("Error getting initial state:", err)
		}
		setInterval(async () => {
			try {
				const state = client("updates.getState", {})
				console.log("New state received")
			} catch (err) {
				console.error("Error updating state:", err)
			}
		}, 5000)
	}
}

module.exports = TelegramPuppet
