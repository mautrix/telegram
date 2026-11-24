// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2024 Sumner Evans
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

package connector

import (
	"go.mau.fi/util/l10n"
)

var L = l10n.NewNamespace("mautrix-telegram")

var (
	msgFlowPhoneName         = L.Define("login.flow.phone.name", "Phone Number")
	msgFlowPhoneDescription  = L.Define("login.flow.phone.description", "Login using your Telegram phone number")
	msgFlowQRName            = L.Define("login.flow.qr.name", "QR Code")
	msgFlowQRDescription     = L.Define("login.flow.qr.description", "Login by scanning a QR code from your phone")
	msgFlowBotName           = L.Define("login.flow.bot.name", "Bot token")
	msgFlowBotDescription    = L.Define("login.flow.bot.description", "Log in as a bot using the bot token provided by BotFather")
	msgFlowManualName        = L.Define("login.flow.manual.name", "Manual")
	msgFlowManualDescription = L.Define("login.flow.manual.description", "Log in using existing session credentials (advanced, do not use)")

	msgFieldPhoneName            = L.Define("login.field.phone_number.name", "Phone number")
	msgFieldPhoneDescription     = L.Define("login.field.phone_number.description", "Include the country code with +")
	msgFieldCodeName             = L.Define("login.field.code.name", "Code")
	msgFieldCodeDescriptionApp   = L.Define("login.field.code.description.app", "The code was sent to the Telegram app on your phone")
	msgFieldCodeDescriptionSMS   = L.Define("login.field.code.description.sms", "The code was sent to your phone via SMS")
	msgFieldCodeDescriptionCall  = L.Define("login.field.code.description.call", "The code will be read to you in a phone call")
	msgFieldCodeDescriptionEmail = L.Define(
		"login.field.code.description.email", "The code was sent to your email ({email})",
		l10n.WithComment("email is a partially masked email address from Telegram, e.g. a***@example.com"),
	)
	msgFieldCodeDescriptionFragment = L.Define(
		"login.field.code.description.fragment", "The code was sent to your Fragment account ({url})",
		l10n.WithComment("url is the Fragment link where the code can be viewed"),
	)
	msgFieldPasswordName    = L.Define("login.field.password.name", "Password")
	msgFieldBotTokenName    = L.Define("login.field.bot_token.name", "Bot token")
	msgFieldSessionDataName = L.Define("login.field.session_data.name", "Session data JSON")

	msgStepPhoneInvalidInstructions      = L.Define("login.step.phone_number_invalid.instructions", "Invalid phone number, please try again")
	msgStepCodeInstructions              = L.Define("login.step.code.instructions", "Enter the login code Telegram sent you")
	msgStepCodeIncorrectInstructions     = L.Define("login.step.code_incorrect.instructions", "Incorrect code")
	msgStepPasswordInstructions          = L.Define("login.step.password.instructions", "You have two-factor authentication enabled.")
	msgStepPasswordIncorrectInstructions = L.Define("login.step.password_incorrect.instructions", "Incorrect password, please try again. Use the official Telegram app to reset your password if you've forgotten it.")
	msgStepQRInstructions                = L.Define("login.step.qr.instructions", "Scan the QR code on your phone to log in")
	msgStepBotTokenInstructions          = L.Define("login.step.bot_token.instructions", "Please enter the bot token you want to log in as")
	msgStepCompleteInstructions          = L.Define(
		"login.step.complete.instructions", "Successfully logged in as {name} (`{id}`)",
		l10n.WithComment("name is the Telegram account's display name, id is its numeric user ID"),
	)
)
