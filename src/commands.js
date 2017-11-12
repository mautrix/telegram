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
const makePasswordHash = require("telegram-mtproto").plugins.makePasswordHash

class Command {
	constructor(description, usage, func) {
		this.description = description
		this.usage = usage
		this.func = func
	}

	run(app, roomID, args) {
		this.func(args, message =>
			app.botIntent.sendText(roomID, message))
	}
}

const commands = {}

function run(sender, command, args, reply) {
	if (sender.commandStatus) {
		if (command === "cancel") {
			sender.commandStatus = undefined
			reply(`${sender.commandStatus.action} cancelled.`)
			return
		}
		args.unshift(command)
		sender.commandStatus.next(sender, args, reply)
		return
	}
	command = this.commands[command]
	if (!command) {
		reply("Unknown command. Try \"$cmdprefix help\" for help.")
	}
	command(sender, args, reply)
}

commands.cancel = () => "Nothing to cancel."

const enterPassword = (sender, args, reply) => {
	if (args.length === 0) {
		reply("Usage: $cmdprefix <password>")
		return
	}

	const hash = makePasswordHash(sender.commandStatus.salt, args[0])
	sender.checkPassword(hash)
		.then(() => {
			// TODO show who the user logged in as
			reply(`Logged in successfully.`)
			sender.commandStatus = undefined
		}, err => {
			reply(`Login failed: ${err}`)
			console.log(err)
		})
}

const enterCode = (sender, args, reply) => {
	if (args.length === 0) {
		reply("Usage: $cmdprefix <authentication code>")
		return
	}

	sender.signInToTelegram(args[0])
		.then(data => {
			if (data.status === "ok") {
				// TODO show who the user logged in as
				reply(`Logged in successfully.`)
				sender.commandStatus = undefined
			} else if (data.status === "need-password") {
				reply(`You have two-factor authentication enabled. Password hint: ${data.hint} \nEnter your password using "$cmdprefix <password>"`)
				sender.commandStatus = {
					action: "Two-factor authentication",
					next: enterPassword,
					salt: data.salt,
				}
			} else {
				reply(`Unexpected sign in response, status=${data.status}`)
			}
		}, err => {
			reply(`Login failed: ${err}`)
			console.log(err)
		})
}

commands.login = (sender, args, reply) => {
	if (args.length === 0) {
		reply("Usage: $cmdprefix login <phone number>")
		return
	}

	sender.sendTelegramCode(args[0])
		.then(data => {
			reply(`Login code sent to ${args[0]}. \nEnter the code using "$cmdprefix <code>"`)
			sender.commandStatus = {
				action: "Phone code authentication",
				next: enterCode,
			}
			console.log(data)
		}, err => {
			reply(`Failed to send code: ${err}`)
			console.log(err)
		})
}

commands.help = (sender, args, reply) => {
	reply("Help not yet implemented 3:")
}

module.exports = {
	commands,
	run,
}
