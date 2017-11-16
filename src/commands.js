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

const commands = {}

function run(sender, command, args, reply, app) {
	if (sender.commandStatus) {
		if (command === "cancel") {
			reply(`${sender.commandStatus.action} cancelled.`)
			sender.commandStatus = undefined
			return
		}
		args.unshift(command)
		return sender.commandStatus.next(sender, args, reply, app)
	}
	command = this.commands[command]
	if (!command) {
		reply("Unknown command. Try \"$cmdprefix help\" for help.")
		return
	}
	return command(sender, args, reply, app)
}

commands.cancel = () => "Nothing to cancel."

commands.help = (sender, args, reply) => {
	reply("Help not yet implemented 3:")
}


  /////////////////////////////
 // Authentication handlers //
/////////////////////////////

/**
 * Two-factor authentication handler.
 */
const enterPassword = async (sender, args, reply) => {
	if (args.length === 0) {
		reply("Usage: $cmdprefix <password>")
		return
	}

	const hash = makePasswordHash(sender.commandStatus.salt, args[0])
	try {
		await sender.checkPassword(hash)
		reply(`Logged in successfully as @${sender.telegramPuppet.getDisplayName()}.`)
		sender.commandStatus = undefined
	} catch (err) {
		reply(`Login failed: ${err}`)
		console.log(err)
	}
}

/*
 * Login code send handler.
 */
const enterCode = async (sender, args, reply) => {
	if (args.length === 0) {
		reply("Usage: $cmdprefix <authentication code>")
		return
	}

	try {
		const data = await sender.signInToTelegram(args[0])
		if (data.status === "ok") {
			reply(`Logged in successfully as @${sender.telegramPuppet.getDisplayName()}.`)
			sender.commandStatus = undefined
		} else if (data.status === "need-password") {
			reply(`You have two-factor authentication enabled. Password hint: ${data.hint}\nEnter your password using "$cmdprefix <password>"`)
			sender.commandStatus = {
				action: "Two-factor authentication",
				next: enterPassword,
				salt: data.salt,
			}
		} else {
			reply(`Unexpected sign in response, status=${data.status}`)
		}
	} catch (err) {
		// TODO login fails somewhere with TypeError: Cannot read property 'status' of undefined
		reply(`Login failed: ${err}`)
		console.error(err.stack)
	}
}

/*
 * Login code request handler.
 */
commands.login = async (sender, args, reply) => {
	if (args.length === 0) {
		reply("Usage: $cmdprefix login <phone number>")
		return
	}

	try {
		const data = await sender.sendTelegramCode(args[0])
		reply(`Login code sent to ${args[0]}.\nEnter the code using "$cmdprefix <code>"`)
		sender.commandStatus = {
			action: "Phone code authentication",
			next: enterCode,
		}
	} catch (err) {
		reply(`Failed to send code: ${err}`)
		console.log(err)
	}
}

commands.register = async (sender, args, reply) => {
	reply("Registration has not yet been implemented. Please use the offical apps for now.")
}

commands.logout = async (sender, args, reply) => {
	try {
		sender.logOutFromTelegram()
		reply("Logged out successfully.")
	} catch (err) {
		reply(`Failed to log out: ${err}`)
	}
}

const TelegramPeer = require("./telegram-peer")
const Portal = require("./portal")

commands.createRoom = async (sender, args, reply, app) => {
	let peer = new TelegramPeer(args[0], +args[1])
	const portal = await app.getPortalByPeer(peer)
	const roomID = await portal.createMatrixRoom(sender.telegramPuppet)
	if (!roomID) {
		reply("Failed to create room.")
		return
	}
	await app.botIntent.invite(roomID, sender.userID)
	reply(`Created room ${roomID} and invited ${sender.userID}`)
}

commands.syncUsers = async (sender, args, reply, app) => {
	let peer = new TelegramPeer(args[0], +args[1])
	const portal = await app.getPortalByPeer(peer)
	try {
		await portal.syncTelegramUsers(sender.telegramPuppet)
		reply("Users synchronized successfully.")
	} catch (err) {
		reply(`Failed to sync users: ${err}`)
		console.error(err)
		console.error(err.stack)
	}
}

  //////////////////////////////
 // General command handlers //
//////////////////////////////


  ////////////////////////////
 // Debug command handlers //
////////////////////////////

commands.api = async (sender, args, reply, app) => {
	if (!app.config.telegram.allow_direct_api_calls) {
		reply("Direct API calls are forbidden on this mautrix-telegram instance.")
		return
	}
	const apiMethod = args.shift()
	let apiArgs
	try {
		apiArgs = JSON.parse(args.join(" "))
	} catch (err) {
		reply("Invalid API method parameters. Usage: $cmdprefix api <method> <json data>")
		return
	}
	try {
		reply(`Calling ${apiMethod} with the following arguments:\n${JSON.stringify(apiArgs, "", "  ")}`)
		const response = await sender.telegramPuppet.client(apiMethod, apiArgs)
		reply(`API call successful. Response:\n${JSON.stringify(response, "", "  ")}`)
	} catch (err) {
		reply(`API call errored. Response:\n${JSON.stringify(err, "", "  ")}`)
	}
}

module.exports = {
	commands,
	run,
}
