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
 *
 * WARNING: This module contains headache-causing regular expressions and other duct tape.
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
 *
 * WARNING: I am not responsible for possible severe headaches caused by reading any part of this function.
 *          While there are a few explaining comments, I haven't even tried to figure out why it works.
 *          The tag priorities are especially non-understandable. You have been warned.
 *
 * @param {string} message  The plaintext message.
 * @param {Array}  entities The Telegram formatting entities.
 */
function telegramToMatrix(message, entities) {
	const tags = []
	// Decreasing priority counter used to ensure that formattings right next to eachother don't flip like this:
	// *bold*_italic_  -->   <strong>bold<em></strong>italic</em>
	let pc = 9001

	// Convert Telegram formatting entities into a weird custom indexed HTML tag format thingy.
	for (const entity of entities) {
		let url, tag
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
		case "messageEntityMention":
			// TODO bridge mentions properly?
			addTag(tags, entity, "font", "color=\"red\"", --pc)
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
const paragraphs = /<p>(.*?)<\/p>/g
const headers = /<h([0-6])>(.*?)<\/h[0-6]>/g
const unorderedLists = /<ul>((.|\n)*?)<\/ul>/g
const orderedLists = /<ol>((.|\n)*?)<\/ol>/g
const listEntries = /<li>(.*?)<\/li>/g

// Formatting that is converted to Telegram entity formatting
const boldText = /<strong>((.|\n)*?)<\/strong>/g
const italicText = /<em>((.|\n)*?)<\/em>/g
const codeblocks = /<pre><code>((.|\n)*?)<\/code><\/pre>/g
const codeblocksWithSyntaxHighlight = /<pre><code class="language-(.*?)">((.|\n)*?)<\/code><\/pre>/g
const inlineCode = /<code>(.*?)<\/code>/g
const emailAddresses = /<a href="mailto:(.*?)">((.|\n)*?)<\/a>/g
const hyperlinks = /<a href="(.*?)">((.|\n)*?)<\/a>/g

const linebreaks = /<br(.*?)>(\n)?/g

/**
 * Convert a Matrix HTML-formatted message to a Telegram entity-formatted message.
 *
 * @param   {string}                             message The HTML-formatted message.
 * @returns {{message: string, entities: Array}}         The Telegram entity-formatted message.
 */
function matrixToTelegram(message) {
	const entities = []
	message = message.replace(linebreaks, "\n")
	message = message.replace(paragraphs, "$1\n")
	message = message.replace(headers, (_, count, text) => `${"#".repeat(count)} ${text}`)
	message = message.replace(unorderedLists, (_, list) => {
		return list.replace(listEntries, "- $1")
	})
	message = message.replace(orderedLists, (_, list) => {
		let n = 0
		return list.replace(listEntries, (fullMatch, text) => `${++n}. ${text}`)
	})
	message = message.replace(boldText, (_, text, index) => {
		entities.push({
			_: "messageEntityBold",
			offset: index,
			length: text.length,
		})
		return text
	})
	message = message.replace(italicText, (_, text, index) => {
		entities.push({
			_: "messageEntityItalic",
			offset: index,
			length: text.length,
		})
		return text
	})
	message = message.replace(codeblocks, (_, text, index) => {
		entities.push({
			_: "messageEntityPre",
			offset: index,
			length: text.length,
			language: "",
		})
		return text
	})
	message = message.replace(codeblocksWithSyntaxHighlight, (_, language, text, index) => {
		entities.push({
			_: "messageEntityPre",
			offset: index,
			length: text.length,
			language,
		})
		return text
	})
	message = message.replace(inlineCode, (_, text, index) => {
		entities.push({
			_: "messageEntityCode",
			offset: index,
			length: text.length,
		})
		return text
	})
	message = message.replace(emailAddresses, (_, address, text, index) => {
		entities.push({
			_: "messageEntityEmail",
			offset: index,
			length: address.length,
		})
		return address
	})
	message = message.replace(hyperlinks, (_, url, text, index) => {
		if (url === text) {
			entities.push({
				_: "messageEntityUrl",
				offset: index,
				length: text.length,
			})
		} else {
			entities.push({
				_: "messageEntityTextUrl",
				offset: index,
				length: text.length,
				url,
			})
		}
		return text
	})
	console.log(entities)
	return { message, entities }
}

module.exports = { telegramToMatrix, matrixToTelegram }
