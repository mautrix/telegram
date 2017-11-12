#!/usr/bin/env node
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
const {AppServiceRegistration} = require("matrix-appservice-bridge")
const commander = require("commander")
const YAML = require("yamljs")
const fs = require("fs")
const MautrixTelegram = require("./app")
const pkg = require("../package.json")

commander
	.version(pkg.version)
	.option("-c, --config <path>", "the file to load the config from. defaults to ./config.yaml")
	.option("-g, --generate-registration", "generate a registration based on the config")
	.option("-r, --registration <path>", "the file to save the registration to. defaults to ./registration.yaml")
	.parse(process.argv)

commander.registration = commander.registration || "./registration.yaml"
commander.config = commander.config || "./config.yaml"

const config = YAML.load(commander.config)

if (commander.generateRegistration) {
	const registration = {
		id: config.appservice.id,
		hs_token: AppServiceRegistration.generateToken(),
		as_token: AppServiceRegistration.generateToken(),
		namespaces: {
			users: [{
				exclusive: true,
				regex: `@${config.bridge.username_template.replace("${ID}", ".+")}:${config.homeserver.domain}`
			}],
			aliases: [],
			rooms: [],
		},
		url: `${config.appservice.protocol}://${config.appservice.hostname}:${config.appservice.port}`,
		sender_localpart: config.bridge.bot_username,
		rate_limited: false,
	}
	fs.writeFileSync(commander.registration, YAML.stringify(registration, 10))
	config.appservice.registration = commander.registration
	fs.writeFileSync(commander.config, YAML.stringify(config, 10))
	return
}

const app = new MautrixTelegram(config)
app.run()
