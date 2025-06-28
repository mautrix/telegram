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
	"sync"

	"maunium.net/go/mautrix/bridgev2/commands"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

var cmdSync = &commands.FullHandler{
	Func: fnSync,
	Name: "sync",
	Help: commands.HelpMeta{
		Section:     commands.HelpSectionGeneral,
		Description: "Synchronize your chat portals, contacts and/or own info.",
		Args:        "[`chats`|`contacts`|`me`]",
	},
	RequiresLogin: true,
}

func fnSync(ce *commands.Event) {
	var only string
	if len(ce.Args) > 0 {
		if !slices.Contains([]string{"chats", "contacts", "me"}, ce.Args[0]) {
			ce.Reply("Invalid argument. Use `chats`, `contacts` or `me`.")
			return
		}
		only = ce.Args[0]
	}

	var wg sync.WaitGroup
	for _, login := range ce.User.GetUserLogins() {
		client := login.Client.(*TelegramClient)
		if only == "" || only == "chats" {
			ce.Reply("Synchronizing chats for %s...", login.ID)
			wg.Add(1)
			go func() {
				defer wg.Done()
				if err := client.SyncChats(ce.Ctx); err != nil {
					ce.Reply("Failed to synchronize chats for %s: %v", login.ID, err)
				}
			}()
		}
		if only == "" || only == "contacts" {
			ce.Reply("Synchronizing contacts...")
			wg.Add(1)
			go func() {
				// TODO
				ce.Reply("Contact sync is not yet implemented!")
				defer wg.Done()
			}()
		}
		if only == "" || only == "me" {
			ce.Reply("Synchronizing your info...")
			wg.Add(1)
			go func() {
				wg.Done()
				if users, err := client.client.API().UsersGetUsers(ce.Ctx, []tg.InputUserClass{&tg.InputUserSelf{}}); err != nil {
					ce.Reply("Failed to get your info for %s: %v", login.ID, err)
				} else if len(users) == 0 {
					ce.Reply("Failed to get your info for %s: no users returned", login.ID)
				} else if users[0].TypeID() != tg.UserTypeID {
					ce.Reply("Unexpected user type %s", users[0].TypeName())
				} else if _, err = client.updateGhost(ce.Ctx, client.telegramUserID, users[0].(*tg.User)); err != nil {
					ce.Reply("Failed to update your info for %s: %v", login.ID, err)
				}
			}()
		}
	}
	wg.Wait()
}
