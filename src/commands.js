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

/**
 * Module containing all management commands.
 *
 * @module commands
 */

/**
 * Run management command.
 *
 * @param {string}          sender  The MXID of the user who sent the command.
 * @param {string}          command The command itself.
 * @param {Array<string>}   args    A list of arguments.
 * @param {function}        reply   A function that is called to reply to the command.
 * @param {MautrixTelegram} app     The MautrixTelegram instance.
 * @param {MatrixEvent}     evt     The event that caused this call.
 */
function run(sender, command, args, reply, app, evt) {
	const commandFunc = this.commands[command]
	if (!commandFunc) {
		if (sender.commandStatus) {
			if (command === "cancel") {
				reply(`${sender.commandStatus.action} cancelled.`)
				sender.commandStatus = undefined
				return undefined
			}
			args.unshift(command)
			return sender.commandStatus.next(sender, args, reply, app, evt)
		}
		reply("Unknown command. Try `$cmdprefix help` for help.")
		return undefined
	}
	try {
		return commandFunc(sender, args, reply, app, evt)
	} catch (err) {
		reply(`Error running command: ${err}.`)
		if (err instanceof Error) {
			reply(["```", err.stack, "```"].join(""))
			console.error(err.stack)
		}
	}
	return undefined
}

commands.cancel = () => "Nothing to cancel."

commands.help = (sender, args, reply, app, evt) => {
	let replyMsg = ""
	if (app.managementRooms.includes(evt.room_id)) {
		replyMsg += "This is a management room: prefixing commands with `$cmdprefix` is not required.\n"
	} else {
		replyMsg += "**This is not a management room**: you must prefix commands with `$cmdprefix`.\n"
	}
	replyMsg += `
_**Generic bridge commands**: commands for using the bridge that aren't related to Telegram._<br/>
**help** - Show this help message.<br/>
**cancel** - Cancel an ongoing action (such as login).<br/>
**setManagement** - Mark the room as a management room.<br/>
**unsetManagement** - Undo management room marking.

_**Telegram actions**: commands for using the bridge to interact with Telegram._<br/>
**login** &lt;_phone_&gt; - Request an authentication code.<br/>
**logout** - Log out from Telegram.<br/>
**search** [_-r|--remote_] &lt;_query_&gt; - Search your contacts or the Telegram servers for users.

_**Temporary commands**: commands that will be replaced with more Matrix-y actions later._<br/>
**pm** &lt;_id_&gt; - Open a private chat with the given Telegram user ID.

_**Debug commands**: commands to help in debugging the bridge. Disabled by default._<br/>
**api** &lt;_method_&gt; &lt;_args_&gt; - Call a Telegram API method. Args is always a single JSON object.
`
	reply(replyMsg, { allowHTML: true })
}

commands.setManagement = (sender, _, reply, app, evt) => {
	app.managementRooms.push(evt.room_id)
	reply("Room marked as management room. You can now run commands without the `$cmdprefix` prefix.")
}

commands.unsetManagement = (sender, _, reply, app, evt) => {
	app.managementRooms.splice(app.managementRooms.indexOf(evt.room_id), 1)
	reply("Room unmarked as management room. You must now include the `$cmdprefix` prefix when running commands.")
}


/////////////////////////////
// Authentication handlers //
/////////////////////////////

/**
 * Two-factor authentication handler.
 */
commands.enterPassword = async (sender, args, reply) => {
	if (args.length === 0) {
		reply("**Usage:** `$cmdprefix <password>`")
		return
	}

	const hash = makePasswordHash(sender.commandStatus.salt, args[0])
	try {
		await sender.checkPassword(hash)
		reply(`Logged in successfully as ${sender.telegramPuppet.getDisplayName()}.`)
		sender.commandStatus = undefined
	} catch (err) {
		reply(`**Login failed:** ${err}`)
		if (err instanceof Error) {
			reply(["```", err.stack, "```"].join(""))
			console.error(err.stack)
		}
	}
}

/*
 * Login code send handler.
 */
commands.enterCode = async (sender, args, reply) => {
	if (args.length === 0) {
		reply("**Usage:** `$cmdprefix <authentication code>`")
		return
	}

	try {
		const data = await sender.signInToTelegram(args[0])
		if (data.status === "ok") {
			reply(`Logged in successfully as ${sender.telegramPuppet.getDisplayName()}.`)
			sender.commandStatus = undefined
		} else if (data.status === "need-password") {
			reply(`You have two-factor authentication enabled. Password hint: ${data.hint}
Enter your password using \`$cmdprefix <password>\``)
			sender.commandStatus = {
				action: "Two-factor authentication",
				next: commands.enterPassword,
				salt: data.salt,
			}
		} else {
			reply(`Unexpected sign in response, status=${data.status}`)
		}
	} catch (err) {
		reply(`**Login failed:** ${err}`)
		if (err instanceof Error) {
			reply(["```", err.stack, "```"].join(""))
			console.error(err.stack)
		}
	}
}

/*
 * Login code request handler.
 */
commands.login = async (sender, args, reply) => {
	if (args.length === 0) {
		reply("**Usage:** `$cmdprefix login <phone number>`")
		return
	}

	try {
		/*const data = */
		await sender.sendTelegramCode(args[0])
		reply(`Login code sent to ${args[0]}.\nEnter the code using \`$cmdprefix <code>\``)
		sender.commandStatus = {
			action: "Phone code authentication",
			next: commands.enterCode,
		}
	} catch (err) {
		reply(`**Failed to send code:** ${err}`)
		if (err instanceof Error) {
			reply(["```", err.stack, "```"].join(""))
			console.error(err.stack)
		}
	}
}

commands.register = async (sender, args, reply) => {
	reply("Registration has not yet been implemented. Please use the official apps for now.")
}

commands.logout = async (sender, args, reply) => {
	try {
		sender.logOutFromTelegram()
		reply("Logged out successfully.")
	} catch (err) {
		reply(`**Failed to log out:** ${err}`)
		if (err instanceof Error) {
			reply(["```", err.stack, "```"].join(""))
			console.error(err.stack)
		}
	}
}

//////////////////////////////
// General command handlers //
//////////////////////////////

commands.search = async (sender, args, reply, app) => {
	if (args.length < 1) {
		reply("Usage: $cmdprefix search [-r|--remote] <query>")
		return
	}
	const msg = []
	if (args[0] !== "-r" && args[0] !== "--remote") {
		const contactResults = await sender.searchContacts(args.join(" "))
		if (contactResults.length > 0) {
			msg.push("**Following results found from local contacts:**")
			msg.push("")
			for (const { match, contact } of contactResults) {
				msg.push(`- <a href="${
					app.getMatrixToLinkForTelegramUser(contact.id)}">${contact.getDisplayName()}</a>: ${contact.id} (${match}% match)`)
			}
			msg.push("")
			msg.push("To force searching from Telegram servers, add `-r` before the search query.")
			reply(msg.join("\n"))
			return
		}
	} else {
		args.shift()
		msg.push("-r flag found: forcing remote search")
		msg.push("")
	}
	const query = args.join(" ")
	if (query.length < 5) {
		reply("Failed to search server: Query is too short.")
		return
	}
	const telegramResults = await sender.searchTelegram(query)
	if (telegramResults.length > 0) {
		msg.push("**Following results received from Telegram server:**")
		for (const user of telegramResults) {
			msg.push(`- <a href="${
				app.getMatrixToLinkForTelegramUser(user.id)}">${user.getDisplayName()}</a>: ${user.id}`)
		}
	} else {
		msg.push("**No users found.**")
	}
	reply(msg.join("\n"))
}

commands.pm = async (sender, args, reply, app) => {
	if (args.length < 1) {
		reply("Usage: $cmdprefix pm <id>")
		return
	}
	const user = await app.getTelegramUser(+args[0], { createIfNotFound: false })
	if (!user) {
		reply("User info not saved. Try searching for the user first?")
		return
	}
	const peer = user.toPeer(sender.telegramPuppet)

	const userInfo = await peer.getInfo(sender.telegramPuppet)
	await user.updateInfo(sender.telegramPuppet, userInfo)

	const portal = await app.getPortalByPeer(peer)
	await portal.createMatrixRoom(sender.telegramPuppet, {
		invite: [sender.userID],
	})
}

////////////////////////////
// Debug command handlers //
////////////////////////////

commands.api = async (sender, args, reply, app) => {
	if (!app.config.bridge.commands.allow_direct_api_calls) {
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
