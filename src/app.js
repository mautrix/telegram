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
const {Bridge} = require("matrix-appservice-bridge")
const crypto = require("crypto")
const commands = require("./commands")
const MatrixUser = require("./matrix-user")
const YAML = require("yamljs")

class MautrixTelegram {
	constructor(config) {
		this.config = config

		this.matrixUsersByID = new Map()
		this.telegramUsersByID = new Map()

		const self = this
		this.bridge = new Bridge({
			homeserverUrl: config.homeserver.address,
			domain: config.homeserver.domain,
			registration: config.appservice.registration,
			controller: {
				onUserQuery(user) {
					return {}
				},
				onEvent(request, context) {
					self.handleMatrixEvent(request.getData())
				},
			},
		})
	}

	async run() {
		console.log("Appservice listening on port %s", this.config.appservice.port)
		await this.bridge.run(this.config.appservice.port, {})
		const userEntries = await this.bridge.getUserStore().select({
			type: "matrix",
		})
		for (const entry of userEntries) {
			const user = MatrixUser.fromEntry(this, entry)
			this.matrixUsersByID.set(entry.id, user)
		}
		//	.then(() =>
		//		this.botIntent.setDisplayName(this.config.bridge.bot_displayname))
	}

	get bot() {
		return this.bridge.getBot()
	}

	get botIntent() {
		return this.bridge.getIntent()
	}

	getMatrixUser(id) {
		let user = this.matrixUsersByID.get(id)
		if (user) {
			return Promise.resolve(user)
		}

		return this.bridge.getUserStore().select({
			type: "matrix",
			id,
		}).then(entries => {
			this.matrixUsersByID.get(id)
			if (user) {
				return Promise.resolve(user)
			}

			if (entries.length) {
				user = MatrixUser.fromEntry(this, entries[0])
			} else {
				user = new MatrixUser(this, id)
			}
			this.matrixUsersByID.set(id, user)
			return user
		})
	}

	putUser(user) {
		const entry = user.toEntry()
		const query = {
			type: entry.type,
			id: entry.id,
		}
		return this.bridge.getUserStore().upsert(query, entry)
	}

	handleMatrixEvent(evt) {
		const asBotID = this.bridge.getBot().getUserId()
		if (evt.type === "m.room.member" && evt.state_key === asBotID) {
			if (evt.content.membership === "invite") {
				// Accept all invites
				this.botIntent.join(evt.room_id)
					.catch(err => {
						console.warn(`Failed to join room ${evt.room_id}:`, err)
						if (e instanceof Error) {
							console.warn(e.stack)
						}
					})
			}
			return
		}
		if (evt.sender === asBotID || evt.type !== "m.room.message" || !evt.content) {
			// Ignore own messages and non-message events.
			return;
		}
		const cmdprefix = this.config.bridge.command_prefix
		if (evt.content.body.startsWith(cmdprefix + " ")) {
			this.getMatrixUser(evt.sender).then(user => {
				if (!user.whitelisted) {
					this.botIntent.sendText(evt.room_id, "You are not authorized to use this bridge.")
					return
				}

				const prefixLength = cmdprefix.length + 1
				const args = evt.content.body.substr(prefixLength).split(" ")
				const command = args.shift()
				commands.run(user, command, args, reply =>
					this.botIntent.sendText(
						evt.room_id,
						reply.replace("$cmdprefix", cmdprefix)),
					this)
			})
			return
		}
	}

	checkWhitelist(userID) {
		if (!this.config.bridge.whitelist || this.config.bridge.whitelist.length === 0) {
			return true
		}

		userID = userID.toLowerCase()
		const userIDCapture = /\@.+\:(.+)/.exec(userID)
		const homeserver = userIDCapture && userIDCapture.length > 1 ? userIDCapture[1] : undefined
		for (let whitelisted of this.config.bridge.whitelist) {
			whitelisted = whitelisted.toLowerCase()
			if (whitelisted === userID || (homeserver && whitelisted === homeserver)) {
				return true
			}
		}
		return false
	}

	encrypt(value) {
		var cipher = crypto.createCipher("aes-256-gcm", this.config.bridge.auth_key_password);
		var ret = cipher.update(Buffer.from(value), "hex", "base64");
		ret += cipher.final("base64");

		return [ret, cipher.getAuthTag().toString("base64")];
	}

	decrypt(value) {
		if(!value) return value;

		var decipher = crypto.createDecipher("aes-256-gcm", this.config.bridge.auth_key_password);
		decipher.setAuthTag(new Buffer(value[1], "base64"));
		var ret = decipher.update(value[0], "base64", "hex");
		ret += decipher.final("hex");

		return ret;
	};
}

module.exports = MautrixTelegram
