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
const Portal = require("./portal")

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
 * @param {object}          extra   Extra information that the handlers may find useful.
 * @param {MautrixTelegram} extra.app          The app main class instance.
 * @param {MatrixEvent}     extra.evt          The event that caused this call.
 * @param {string}          extra.roomID       The ID of the Matrix room the command was sent to.
 * @param {boolean}         extra.isManagement Whether or not the Matrix room is a management room.
 * @param {boolean}         extra.isPortal     Whether or not the Matrix room is a portal to a Telegram chat.
 */
function run(sender, command, args, reply, extra) {
	const commandFunc = this.commands[command]
	if (!commandFunc) {
		if (sender.commandStatus) {
			if (command === "cancel") {
				reply(`${sender.commandStatus.action} cancelled.`)
				sender.commandStatus = undefined
				return undefined
			}
			args.unshift(command)
			return sender.commandStatus.next(sender, args, reply, extra)
		}
		reply("Unknown command. Try `$cmdprefix help` for help.")
		return undefined
	}
	try {
		return commandFunc(sender, args, reply, extra)
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

commands.help = (sender, args, reply, { isManagement, isPortal }) => {
	let replyMsg = ""
	if (isManagement) {
		replyMsg += "This is a management room: prefixing commands with `$cmdprefix` is not required.\n"
	} else if (isPortal) {
		replyMsg += "**This is a portal room**: you must always prefix commands with `$cmdprefix`.\n" +
			"Management commands will not be sent to Telegram.\n"
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
**search** [_-r|--remote_] &lt;_query_&gt; - Search your contacts or the Telegram servers for users.<br/>
**create** &lt;_group/channel_&gt; [_room ID_] - Create a Telegram chat of the given type for a Matrix room.
                                           If the room ID is not specified, a chat for the current room is created.<br/>
**upgrade** - Upgrade a normal Telegram group to a supergroup.

_**Temporary commands**: commands that will be replaced with more Matrix-y actions later._<br/>
**pm** &lt;_id_&gt; - Open a private chat with the given Telegram user ID.

_**Debug commands**: commands to help in debugging the bridge. Disabled by default._<br/>
**api** &lt;_method_&gt; &lt;_args_&gt; - Call a Telegram API method. Args is always a single JSON object.
`
	reply(replyMsg, { allowHTML: true })
}

commands.setManagement = (sender, _, reply, { app, roomID, isPortal }) => {
	if (isPortal) {
		reply("You may not mark portal rooms as management rooms.")
		return
	}
	app.managementRooms.push(roomID)
	reply("Room marked as management room. You can now run commands without the `$cmdprefix` prefix.")
}

commands.unsetManagement = (sender, _, reply, { app, roomID }) => {
	app.managementRooms.splice(app.managementRooms.indexOf(roomID), 1)
	reply("Room unmarked as management room. You must now include the `$cmdprefix` prefix when running commands.")
}


/////////////////////////////
// Authentication handlers //
/////////////////////////////

/**
 * Two-factor authentication handler.
 */
commands.enterPassword = async (sender, args, reply, { isManagement }) => {
	if (!isManagement) {
		reply("Logging in is considered a confidential action, and thus is only allowed in management rooms.")
		return
	} else if (args.length === 0) {
		reply("**Usage:** `$cmdprefix <password> [salt]`")
		return
	}

	let salt

	if (!sender.commandStatus || !sender.commandStatus.salt) {
		if (args.length > 1) {
			salt = args[1]
		} else {
			reply("No password salt found. Did you enter your phone code already?")
			return
		}
	} else {
		salt = sender.commandStatus.salt
	}

	const hash = makePasswordHash(salt, args[0])
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
commands.enterCode = async (sender, args, reply, { isManagement }) => {
	if (!isManagement) {
		reply("Logging in is considered a confidential action, and thus is only allowed in management rooms.")
		return
	} else 	if (args.length === 0) {
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
commands.login = async (sender, args, reply, { isManagement }) => {
	if (!isManagement) {
		reply("Logging in is considered a confidential action, and thus is only allowed in management rooms.")
		return
	} else if (args.length === 0) {
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

commands.create = async (sender, args, reply, { app, roomID }) => {
	if (args.length < 1 || (args[0] !== "group" && args[0] !== "channel")) {
		reply("**Usage:** `$cmdprefix create <group/channel>`")
		return
	} else if (!sender._telegramPuppet) {
		reply("This command requires you to be logged in.")
		return
	} else if (args[0] === "channel") {
		reply("Creating channels is not yet supported.")
		return
	}

	if (args.length > 1) {
		roomID = args[1]
	}

	// TODO make sure that the AS bot is in the room.

	const title = await app.getRoomTitle(roomID)
	if (!title) {
		reply("Please set a room name before creating a Telegram chat.")
		return
	}

	let portal = await app.getPortalByRoomID(roomID)
	if (portal) {
		reply("This is already a portal room.")
		return
	}

	portal = new Portal(app, roomID)
	try {
		await portal.createTelegramChat(sender.telegramPuppet, title)
		reply(`Telegram chat created. ID: ${portal.id}`)
		if (app.managementRooms.includes(roomID)) {
			app.managementRooms.splice(app.managementRooms.indexOf(roomID), 1)
		}
	} catch (err) {
		reply(`Failed to create Telegram chat: ${err}`)
	}
}

commands.upgrade = async (sender, args, reply, { app, roomID }) => {
	if (!sender._telegramPuppet) {
		reply("This command requires you to be logged in.")
		return
	}

	const portal = await app.getPortalByRoomID(roomID)
	if (!portal) {
		reply("This is not a portal room.")
		return
	}

	await portal.upgradeTelegramChat(sender.telegramPuppet)
}

commands.search = async (sender, args, reply, { app }) => {
	if (args.length < 1) {
		reply("**Usage:** `$cmdprefix search [-r|--remote] <query>`")
		return
	} else if (!sender._telegramPuppet) {
		reply("This command requires you to be logged in.")
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
			reply(msg.join("\n"), { allowHTML: true })
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
	reply(msg.join("\n"), { allowHTML: true })
}

commands.pm = async (sender, args, reply, { app }) => {
	if (args.length < 1) {
		reply("**Usage:** `$cmdprefix pm <id>`")
		return
	} else if (!sender._telegramPuppet) {
		reply("This command requires you to be logged in.")
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

commands.api = async (sender, args, reply, { app }) => {
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
		reply(`API call successful. Response:

<pre><code class="language-json">
  ${JSON.stringify(response, "", "  ")}
</code></pre>`, { allowHTML: true })
	} catch (err) {
		reply(`API call errored. Response:\n${JSON.stringify(err, "", "  ")}`)
	}
}

function timeout(promise, ms = 2500) {
	return new Promise((resolve, reject) => {
		promise.then(resolve, reject)
		setTimeout(() => reject(new Error("API call response not received")), ms)
	})
}

commands.ping = async (sender, args, reply) => {
	try {
		await timeout(sender.telegramPuppet.client("contacts.getContacts", {}))
		reply("Connection seems OK.")
	} catch (err) {
		reply(`Not connected: ${err}`)
	}
}

module.exports = {
	commands,
	run,
}
