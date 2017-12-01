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

/**
 * Utility functions to convert between Telegram and Matrix (HTML) formatting.
 * <br><br>
 * <b>WARNING: This module contains headache-causing regular expressions and other duct tape.</b>
 *
 * @module formatter
 */

String.prototype.insert = function(at, str) {
	return this.slice(0, at) + str + this.slice(at)
}

function addSimpleTag(tags, entity, tag, priority = 0) {
	tags.push([entity.offset, `<${tag}>`, -priority])
	tags.push([entity.offset + entity.length, `</${tag}>`, priority])
}

function addTag(tags, entity, tag, attrs, priority = 0) {
	tags.push([entity.offset, `<${tag} ${attrs}>`, -priority])
	tags.push([entity.offset + entity.length, `</${tag}>`, priority])
}

/**
 * Convert a Telegram entity-formatted message to a Matrix HTML-formatted message.
 * <br><br>
 * <b>WARNING: I am not responsible for possible severe headaches caused by reading any part of this function.</b>
 *
 * @param {string} message  The plaintext message.
 * @param {Array}  entities The Telegram formatting entities.
 * @param {MautrixTelegram} app The app main class instance to use when reformatting mentions.
 */
function telegramToMatrix(message, entities, app) {
	const tags = []
	// Decreasing priority counter used to ensure that formattings right next to eachother don't flip like this:
	// *bold*_italic_  -->   <strong>bold<em></strong>italic</em>
	let pc = 9001

	// Convert Telegram formatting entities into a weird custom indexed HTML tag format thingy.
	for (const entity of entities) {
		let url, tag, mxid
		switch (entity._) {
		case "messageEntityBold":
			tag = tag || "strong"
		case "messageEntityItalic":
			tag = tag || "em"
		case "messageEntityCode":
			tag = tag || "code"
			addSimpleTag(tags, entity, tag, --pc)
			break
		case "messageEntityPre":
			pc--
			addSimpleTag(tags, entity, "pre", pc)
			addTag(tags, entity, "code", `class="language-${entity.language}"`, pc + 1)
			break
		case "messageEntityHashtag":
		case "messageEntityBotCommand":
			// TODO bridge bot commands differently?
			addTag(tags, entity, "font", "color=\"blue\"", --pc)
			break
		case "messageEntityMentionName":
			let user = app.matrixUsersByTelegramID.get(entity.user_id)
			if (!user) {
				// TODO this loop step should be made useless
				for (const userByMXID of app.matrixUsersByID.values()) {
					if (userByMXID.telegramUserID === entity.user_id) {
						user = userByMXID
						app.matrixUsersByTelegramID.set(userByMXID.telegramUserID, userByMXID)
						break
					}
				}
			}
			mxid = user ?
				user.userID :
				app.getMXIDForTelegramUser(entity.user_id)
		case "messageEntityMention":
			if (!mxid) {
				const username = message.substr(entity.offset + 1, entity.length - 1)
				for (const userByMXID of app.matrixUsersByID.values()) {
					if (userByMXID._telegramPuppet && userByMXID._telegramPuppet.data.username === username) {
						mxid = userByMXID.userID
						break
					}
				}
				if (!mxid) {
					for (const userByID of app.telegramUsersByID.values()) {
						if (userByID.username === username) {
							mxid = userByID.mxid
							break
						}
					}
				}
			}

			if (!mxid) {
				continue
			}
			addTag(tags, entity, "a", `href="https://matrix.to/#/${mxid}"`)
			break
		case "messageEntityEmail":
			url = url || `mailto:${message.substr(entity.offset, entity.length)}`
		case "messageEntityUrl":
			url = url || message.substr(entity.offset, entity.length)
		case "messageEntityTextUrl":
			url = url || entity.url
			addTag(tags, entity, "a", `href="${url}"`, --pc)
			break
		}
	}

	// Sort tags in a mysterious way (it seems to work, don't touch it!).
	//
	// The important thing is that the tags are sorted last to first,
	// so when replacing by index, the index doesn't need to be adapted.
	tags.sort(([aIndex, , aPriority], [bIndex, , bPriority]) => bIndex - aIndex || aPriority - bPriority)

	// Insert tags into message
	for (const [index, replacement] of tags) {
		message = message.insert(index, replacement)
	}
	return message
}

// Formatting that is converted back to text
const linebreaks = /<br(.*?)>(\n)?/g
const paragraphs = /<p>([^]*?)<\/p>/g
const headers = /<h([0-6])>([^]*?)<\/h[0-6]>/g
const unorderedLists = /<ul>([^]*?)<\/ul>/g
const orderedLists = /<ol>([^]*?)<\/ol>/g
const listEntries = /<li>([^]*?)<\/li>/g

// Formatting that is converted to Telegram entity formatting
const boldText = /<(strong)>()([^]*?)<\/strong>/g
const italicText = /<(em)>()([^]*?)<\/em>/g
const codeblocks = /<(pre><code)>()([^]*?)<\/code><\/pre>/g
const codeblocksWithSyntaxHighlight = /<(pre><code class)="language-(.*?)">([^]*?)<\/code><\/pre>/g
const inlineCode = /<(code)>()(.*?)<\/code>/g
const emailAddresses = /<a href="(mailto):(.*?)">([^]*?)<\/a>/g
const mentions = /<a href="https:\/\/(matrix\.to)\/#\/(@.+?)">(.*?)<\/a>/g
const hyperlinks = /<(a href)="(.*?)">([^]*?)<\/a>/g
const REGEX_CAPTURE_GROUP_COUNT = 3

RegExp.any = function(...regexes) {
	let components = []
	for (const regex of regexes) {
		if (regex instanceof RegExp) {
			components = components.concat(regex._components || regex.source)
		}
	}
	return new RegExp(`(?:${components.join(")|(?:")})`)
}

const regexMonster = RegExp.any(//"g",
		boldText, italicText, codeblocks, codeblocksWithSyntaxHighlight,
		inlineCode, emailAddresses, mentions, hyperlinks)
const NUMBER_OF_REGEXES_EATEN_BY_MONSTER = 8

function regexMonsterMatchParser(match) {
	match.pop() // Remove full string
	const index = match.pop()
	let identifier, arg, text
	for (let i = 0; i < NUMBER_OF_REGEXES_EATEN_BY_MONSTER; i++) {
		if (match[i * REGEX_CAPTURE_GROUP_COUNT]) {
			identifier = match[i * REGEX_CAPTURE_GROUP_COUNT]
			arg = match[(i * REGEX_CAPTURE_GROUP_COUNT) + 1]
			text = match[(i * REGEX_CAPTURE_GROUP_COUNT) + 2]
		}
	}
	return { index, identifier, arg, text }
}

function regexMonsterHandler(identifier, arg, text, index, app) {
	let entity, entityClass, argField
	switch (identifier) {
	case "strong":
		entityClass = "Bold"
		break
	case "em":
		entityClass = "Italic"
		break
	case "pre><code":
	case "pre><code class":
		argField = "language"
		entityClass = "Pre"
		break
	case "code":
		entityClass = "Code"
		break
	case "mailto":
		entityClass = "email"
		// Force text to be the email address
		text = arg
		break
	case "a href":
		if (arg === text) {
			entityClass = "Url"
		} else {
			entityClass = "TextUrl"
			argField = "url"
		}
	case "matrix.to":
		if (app) {
			const match = app.usernameRegex.exec(arg)
			if (!match || match.length < 2) {
				break
			}
			const userID = match[1]

			const user = app.telegramUsersByID.get(+userID)
			if (!user) {
				break
			}

			if (user.username) {
				entityClass = "Mention"
				text = `@${user.username}`
			} else {
				text = user.getDisplayName()
				entity = {
					_: "inputMessageEntityMentionName",
					offset: index,
					length: text.length,
					user_id: {
						_: "inputUser",
						user_id: user.id,
					},
				}
			}
		}
		break
	}
	if (!entity && entityClass) {
		entity = {
			_: `messageEntity${entityClass}`,
			offset: index,
			length: text.length,
		}
		if (argField) {
			entity[argField] = arg
		}
	}
	return { replacement: text, entity }
}

/**
 * Convert a Matrix HTML-formatted message to a Telegram entity-formatted message.
 *
 * @param   {string}                             message The HTML-formatted message.
 * @returns {{message: string, entities: Array}}         The Telegram entity-formatted message.
 */
function matrixToTelegram(message, app) {
	const entities = []

	// First replace all the things that don't get converted into Telegram entities
	message = message.replace(linebreaks, "\n")
	message = message.replace(paragraphs, "$1\n")
	message = message.replace(headers, (_, count, text) => `${"#".repeat(count)} ${text}`)
	message = message.replace(unorderedLists, (_, list) => list.replace(listEntries, "- $1"))
	message = message.replace(orderedLists, (_, list) => {
		let n = 0
		return list.replace(listEntries, (fullMatch, text) => `${++n}. ${text}`)
	})

	const regexMonsterReplacer = (match, ...args) => {
		const { index, identifier, arg, text } = regexMonsterMatchParser(args)
		if (!identifier) {
			// This shouldn't happen
			console.warn(`Warning: Match found but parsing failed for match "${match}"`)
			return match
		}
		const { replacement, entity } = regexMonsterHandler(identifier, arg, text, index, app)
		if (entity) {
			entities.push(entity)
		}
		return replacement || text
	}

	// We replace matches iteratively to make sure the indexes of matches are correct.
	let oldMessage = message
	message = message.replace(regexMonster, regexMonsterReplacer)
	while (oldMessage !== message) {
		oldMessage = message
		message = message.replace(regexMonster, regexMonsterReplacer)
	}

	return { message, entities }
}

module.exports = { telegramToMatrix, matrixToTelegram }
