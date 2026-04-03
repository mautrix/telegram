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
	"errors"
	"regexp"
	"slices"
	"strings"

	"golang.org/x/net/html"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/commands"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/format"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
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

var cmdUpgrade = &commands.FullHandler{
	Func: fnUpgrade,
	Name: "upgrade",
	Help: commands.HelpMeta{
		Section:     commands.HelpSectionChats,
		Description: "Upgrade a minigroup to a supergroup on Telegram",
	},
	RequiresPortal: true,
}

func fnUpgrade(ce *commands.Event) {
	login, _, err := ce.Portal.FindPreferredLogin(ce.Ctx, ce.User, false)
	if errors.Is(err, bridgev2.ErrNotLoggedIn) {
		ce.Reply("No logins found to upgrade the chat.")
	} else if err != nil {
		ce.Log.Err(err).Msg("Failed to find preferred login for upgrade command")
		ce.Reply("Failed to find a login to upgrade the chat.")
	} else if peerType, chatID, _, err := ids.ParsePortalID(ce.Portal.ID); err != nil {
		ce.Log.Err(err).Str("portal_id", string(ce.Portal.ID)).Msg("Failed to parse portal ID for upgrade command")
		ce.Reply("Failed to parse portal ID")
	} else if peerType == ids.PeerTypeChannel {
		ce.Reply("Only minigroups can be upgraded (this is already a channel/supergroup).")
	} else if peerType == ids.PeerTypeUser {
		ce.Reply("Only minigroups can be upgraded (this is direct chat).")
	} else if resp, err := login.Client.(*TelegramClient).client.API().MessagesMigrateChat(ce.Ctx, chatID); err != nil {
		ce.Log.Err(err).Int64("chat_id", chatID).Msg("Failed to upgrade chat")
		ce.Reply("Failed to upgrade chat: %v", err)
	} else {
		ce.Log.Trace().Any("response", resp).Msg("Updates from chat upgrade")
		ce.Log.Info().Int64("old_chat_id", chatID).Msg("Successfully upgraded chat")
		ce.React("\u2705\ufe0f")
		err = login.Client.(*TelegramClient).dispatcher.Handle(ce.Ctx, resp)
		if err != nil {
			ce.Log.Err(err).Msg("Failed to handle updates from chat upgrade")
		} else {
			ce.Log.Debug().Msg("Finished handling updates from chat upgrade")
		}
	}
}

var cmdJoin = &commands.FullHandler{
	Func: fnJoin,
	Name: "join",
	Help: commands.HelpMeta{
		Section:     commands.HelpSectionChats,
		Description: "Join a Telegram group using an invite link.",
		Args:        "[login ID] <invite link>",
	},
	RequiresLogin: true,
}

var usernameLinkRe = regexp.MustCompile(`^(?:(?:https?://)?t(?:elegram)?\.(?:me|dog)/|tg:/{0,2}resolve\?domain=)([a-zA-Z]\w{3,30}[a-zA-Z\d])(?:\?.+)?$`)
var inviteLinkRe = regexp.MustCompile(`^(?:(?:https?://)?t(?:elegram)?\.(?:me|dog)/(?:joinchat/|\+)|tg:/{0,2}join\?invite=)([a-zA-Z0-9_-]{22})(?:\?.+)?$`)

func fnJoin(ce *commands.Event) {
	if len(ce.Args) == 0 || len(ce.Args) > 2 {
		ce.Reply("Usage: `$cmdprefix join [login ID] <invite link>`")
		return
	}
	var login *bridgev2.UserLogin
	if len(ce.Args) == 2 {
		targetLogin := ce.Bridge.GetCachedUserLoginByID(networkid.UserLoginID(ce.Args[0]))
		if targetLogin == nil || targetLogin.UserMXID != ce.User.MXID {
			ce.Reply("No login found with the provided ID.")
			return
		}
		login = targetLogin
		ce.Args = ce.Args[1:]
	} else {
		login = ce.User.GetDefaultLogin()
		if login == nil {
			ce.Reply("You're not logged in.")
			return
		}
	}
	t := login.Client.(*TelegramClient)
	var resp tg.UpdatesClass
	var chatName string
	if usernameMatch := usernameLinkRe.FindStringSubmatch(ce.Args[0]); usernameMatch != nil {
		resolve, err := t.client.API().ContactsResolveUsername(ce.Ctx, &tg.ContactsResolveUsernameRequest{Username: usernameMatch[1]})
		if err != nil {
			ce.Log.Err(err).Msg("Failed to resolve username from invite link")
			ce.Reply("Failed to resolve username from invite link: %v", err)
			return
		}
		peer, isChannel := resolve.Peer.(*tg.PeerChannel)
		if !isChannel {
			ce.Reply("That username does not belong to a channel or supergroup.")
			return
		}
		var ch *tg.Channel
		for _, chat := range resolve.Chats {
			if chat.GetID() == peer.ChannelID {
				ch = chat.(*tg.Channel)
			}
		}
		if ch == nil {
			ce.Reply("Channel information not found in resolve response.")
			return
		}
		chatName = ch.Title
		resp, err = t.client.API().ChannelsJoinChannel(ce.Ctx, ch.AsInput())
		if err != nil {
			ce.Log.Err(err).Msg("Failed to join chat with invite link")
			ce.Reply("Failed to join chat: %v", err)
			return
		}
	} else if inviteLinkMatch := inviteLinkRe.FindStringSubmatch(ce.Args[0]); inviteLinkMatch != nil {
		resolve, err := t.client.API().MessagesCheckChatInvite(ce.Ctx, inviteLinkMatch[1])
		if tgerr.Is(err, tg.ErrInviteHashInvalid) {
			ce.Reply("Invalid invite link.")
			return
		} else if tgerr.Is(err, tg.ErrInviteHashExpired) {
			ce.Reply("Invite link expired.")
			return
		}
		switch typed := resolve.(type) {
		case *tg.ChatInviteAlready:
			titler, ok := typed.Chat.(interface {
				GetTitle() string
			})
			if ok {
				chatName = titler.GetTitle()
			} else {
				chatName = "that chat"
			}
			ce.Reply("You're already a member of %s", html.EscapeString(chatName))
			return
		case *tg.ChatInvite:
			chatName = typed.Title
		default:
			ce.Log.Warn().Type("resolved_type", typed).Msg("Unexpected response type from MessagesCheckChatInvite")
		}
		resp, err = t.client.API().MessagesImportChatInvite(ce.Ctx, inviteLinkMatch[1])
		if err != nil {
			ce.Log.Err(err).Msg("Failed to join chat with invite link")
			ce.Reply("Failed to join chat: %v", err)
			return
		}
	} else {
		ce.Reply("Invalid invite link format.")
		return
	}
	err := t.dispatcher.Handle(ce.Ctx, resp)
	if err != nil {
		ce.Log.Err(err).Msg("Failed to handle updates from joining chat with invite link")
	} else {
		ce.Log.Debug().Msg("Finished handling updates from joining chat with invite link")
	}
	ce.Reply("Successfully joined %s", html.EscapeString(chatName))
}

var cmdEmojiPack = &commands.FullHandler{
	Func:    fnEmojiPack,
	Name:    "emoji-pack",
	Aliases: []string{"pack", "sticker-pack", "emojipack", "stickerpack"},
	Help: commands.HelpMeta{
		Section:     commands.HelpSectionChats,
		Description: "Bridge emoji packs between Matrix and Telegram.",
		Args:        "<upload/download/list/help> [args...]",
	},
	RequiresLogin: true,
}

const emojiPackHelp = `This command can be used to transfer emoji packs between Matrix and Telegram.

* $cmdprefix emoji-pack upload <telegram shortcode> <room ID> <state key> - Transfer a pack from Matrix to Telegram.
* $cmdprefix emoji-pack download <pack shortcode or link> - Transfer a pack from Telegram to Matrix.
* $cmdprefix emoji-pack list - List your current emoji packs on Telegram.
* $cmdprefix emoji-pack help - Show this help message.`

func fnEmojiPack(ce *commands.Event) {
	var login *bridgev2.UserLogin
	if len(ce.Args) > 0 {
		targetLogin := ce.Bridge.GetCachedUserLoginByID(networkid.UserLoginID(ce.Args[0]))
		if targetLogin != nil && targetLogin.UserMXID == ce.User.MXID {
			ce.Args = ce.Args[1:]
			login = targetLogin
		}
	}
	var command string
	if len(ce.Args) > 0 {
		command = strings.ToLower(ce.Args[0])
		ce.Args = ce.Args[1:]
	}

	if login == nil {
		login = ce.User.GetDefaultLogin()
		if login == nil {
			ce.Reply("You're not logged in.")
			return
		}
	}
	client := login.Client.(*TelegramClient)

	switch command {
	case "help", "":
		ce.Reply(emojiPackHelp)
	case "list":
		client.fnListEmojiPacks(ce)
	case "upload":
		client.fnUploadEmojiPack(ce)
	case "download":
		client.fnDownloadEmojiPack(ce)
	default:
		ce.Reply("Usage: `$cmdprefix emoji-pack <upload/download/list/help> [args...]`")
	}
}
