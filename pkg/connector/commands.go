// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
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
	"slices"

	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/commands"
	"maunium.net/go/mautrix/format"
)

var cmdSyncChats = &commands.FullHandler{
	Func: fnSyncChats,
	Name: "sync-chats",
	Help: commands.HelpMeta{
		Section:     commands.HelpSectionChats,
		Description: "Synchronize your chats",
		Args:        "[_login ID_]",
	},
	RequiresLogin: true,
}

func fnSyncChats(ce *commands.Event) {
	logins := ce.User.GetUserLogins()
	if len(ce.Args) > 0 {
		logins = slices.DeleteFunc(logins, func(login *bridgev2.UserLogin) bool {
			return !slices.Contains(ce.Args, string(login.ID))
		})
		if len(logins) == 0 {
			ce.Reply("No matching logins found with provided ID(s)")
			return
		}
	}
	for _, login := range logins {
		client := login.Client.(*TelegramClient)
		if err := client.syncChats(ce.Ctx, 0, false, true); err != nil {
			ce.Reply("Failed to synchronize chats for %s: %v", format.SafeMarkdownCode(login.ID), err)
		} else {
			ce.Reply("Successfully synchronized chats for %s", format.SafeMarkdownCode(login.ID))
		}
	}
}
