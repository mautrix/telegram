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
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/rs/zerolog"
	"golang.org/x/net/html"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/commands"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"
	"maunium.net/go/mautrix/format"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

var helpSectionPortalApproval = commands.HelpSection{Name: "Telegram portal approval", Order: 21}

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
		ce.Reply("No logins found to upgrade the chat")
	} else if err != nil {
		ce.Log.Err(err).Msg("Failed to find preferred login for upgrade command")
		ce.Reply("Failed to find a login to upgrade the chat.")
	} else if peerType, chatID, _, err := ids.ParsePortalID(ce.Portal.ID); err != nil {
		ce.Log.Err(err).Str("portal_id", string(ce.Portal.ID)).Msg("Failed to parse portal ID for upgrade command")
		ce.Reply("Failed to parse portal ID")
	} else if peerType == ids.PeerTypeChannel {
		ce.Reply("Only minigroups can be upgraded (this is already a channel/supergroup)")
	} else if peerType == ids.PeerTypeUser {
		ce.Reply("Only minigroups can be upgraded (this is direct chat)")
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
var inviteLinkRe = regexp.MustCompile(`^(?:(?:https?://)?t(?:elegram)?\.(?:me|dog)/(?:joinchat/|\+)|tg:/{0,2}join\?invite=)([a-zA-Z0-9_-]{8,64})(?:\?.+)?$`)

func fnJoin(ce *commands.Event) {
	if len(ce.Args) == 0 || len(ce.Args) > 2 {
		ce.Reply("Usage: `$cmdprefix join [login ID] <invite link>`")
		return
	}
	var login *bridgev2.UserLogin
	if len(ce.Args) == 2 {
		targetLogin := ce.Bridge.GetCachedUserLoginByID(networkid.UserLoginID(ce.Args[0]))
		if targetLogin == nil || targetLogin.UserMXID != ce.User.MXID {
			ce.Reply("No login found with the provided ID")
			return
		}
		login = targetLogin
		ce.Args = ce.Args[1:]
	} else {
		login = ce.User.GetDefaultLogin()
		if login == nil {
			ce.Reply("You're not logged in")
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
			ce.Reply("That username does not belong to a channel or supergroup")
			return
		}
		var ch *tg.Channel
		for _, chat := range resolve.Chats {
			if chat.GetID() == peer.ChannelID {
				ch = chat.(*tg.Channel)
			}
		}
		if ch == nil {
			ce.Reply("Channel information not found in resolve response")
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
			ce.Reply("Invalid invite link")
			return
		} else if tgerr.Is(err, tg.ErrInviteHashExpired) {
			ce.Reply("Invite link expired")
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
		ce.Reply("Invalid invite link format")
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

var cmdPending = &commands.FullHandler{
	Func: fnApprovalList(store.PortalApprovalPending),
	Name: "pending",
	Help: commands.HelpMeta{
		Section:     helpSectionPortalApproval,
		Description: "List Telegram chats waiting for approval",
	},
	RequiresLogin: true,
}

var cmdAllowed = &commands.FullHandler{
	Func: fnApprovalList(store.PortalApprovalAllowed),
	Name: "allowed",
	Help: commands.HelpMeta{
		Section:     helpSectionPortalApproval,
		Description: "List Telegram chats approved for portal creation",
	},
	RequiresLogin: true,
}

var cmdDenied = &commands.FullHandler{
	Func: fnApprovalList(store.PortalApprovalDenied),
	Name: "denied",
	Help: commands.HelpMeta{
		Section:     helpSectionPortalApproval,
		Description: "List Telegram chats denied for portal creation",
	},
	RequiresLogin: true,
}

var cmdAllow = &commands.FullHandler{
	Func: fnApprovalSetStatus(store.PortalApprovalAllowed, true, false, store.PortalApprovalPending),
	Name: "allow",
	Help: commands.HelpMeta{
		Section:     helpSectionPortalApproval,
		Description: "Approve a Telegram chat from the pending list and create its portal",
		Args:        "<number>",
	},
	RequiresLogin: true,
}

var cmdDeny = &commands.FullHandler{
	Func: fnApprovalSetStatus(store.PortalApprovalDenied, false, true, store.PortalApprovalPending, store.PortalApprovalAllowed, store.PortalApprovalDenied),
	Name: "deny",
	Help: commands.HelpMeta{
		Section:     helpSectionPortalApproval,
		Description: "Deny a Telegram chat and optionally clean up its portal",
		Args:        "<number>",
	},
	RequiresLogin: true,
}

var cmdUnallow = &commands.FullHandler{
	Func:    fnApprovalSetStatus(store.PortalApprovalPending, false, true, store.PortalApprovalAllowed, store.PortalApprovalPending),
	Name:    "unallow",
	Aliases: []string{"disallow"},
	Help: commands.HelpMeta{
		Section:     helpSectionPortalApproval,
		Description: "Move a Telegram chat back to pending and optionally clean up its portal",
		Args:        "<number>",
	},
	RequiresLogin: true,
}

var cmdUndeny = &commands.FullHandler{
	Func:    fnApprovalSetStatus(store.PortalApprovalPending, false, false, store.PortalApprovalDenied, store.PortalApprovalPending),
	Name:    "undeny",
	Aliases: []string{"pardon"},
	Help: commands.HelpMeta{
		Section:     helpSectionPortalApproval,
		Description: "Move a denied Telegram chat back to pending",
		Args:        "<number>",
	},
	RequiresLogin: true,
}

func approvalCommandClient(ce *commands.Event) *TelegramClient {
	login := ce.User.GetDefaultLogin()
	if login == nil {
		ce.Reply("You're not logged in")
		return nil
	}
	client, ok := login.Client.(*TelegramClient)
	if !ok {
		ce.Reply("Your default login is not a Telegram login")
		return nil
	}
	return client
}

func fnApprovalList(status store.PortalApprovalStatus) func(*commands.Event) {
	return func(ce *commands.Event) {
		client := approvalCommandClient(ce)
		if client == nil {
			return
		}
		if err := client.cleanupOldPendingPortalApprovals(ce.Ctx); err != nil {
			ce.Log.Err(err).Msg("Failed to clean old pending Telegram portal approvals")
			ce.Reply("Failed to clean old pending Telegram chats: %v", err)
			return
		}
		items, err := client.main.Store.Approval.GetByStatus(ce.Ctx, client.telegramUserID, status)
		if err != nil {
			ce.Log.Err(err).Str("approval_status", string(status)).Msg("Failed to list Telegram portal approvals")
			ce.Reply("Failed to list %s Telegram chats: %v", status, err)
			return
		} else if len(items) == 0 {
			ce.Reply("No %s Telegram chats.", status)
			return
		}

		var builder strings.Builder
		fmt.Fprintf(&builder, "%s Telegram chats:\n", approvalStatusTitle(status))
		firstSection := true
		printed := map[string]struct{}{}
		for _, peerType := range []string{string(ids.PeerTypeUser), string(ids.PeerTypeChat), "supergroup", string(ids.PeerTypeChannel)} {
			printedAny := false
			for _, item := range items {
				if item.PeerType != peerType {
					continue
				}
				if !printedAny {
					printedAny = true
					writeApprovalSectionHeader(&builder, approvalPeerTypeTitle(peerType), firstSection)
					firstSection = false
				}
				builder.WriteString(formatApprovalItem(item))
				printed[item.PeerType] = struct{}{}
			}
		}
		for _, item := range items {
			if _, ok := printed[item.PeerType]; ok {
				continue
			}
			if _, ok := printed[""]; !ok {
				printed[""] = struct{}{}
				writeApprovalSectionHeader(&builder, approvalPeerTypeTitle(item.PeerType), firstSection)
				firstSection = false
			}
			builder.WriteString(formatApprovalItem(item))
		}
		ce.Reply(builder.String())
	}
}

func fnApprovalSetStatus(status store.PortalApprovalStatus, createPortal, cleanupPortal bool, allowedSourceStatuses ...store.PortalApprovalStatus) func(*commands.Event) {
	return func(ce *commands.Event) {
		client := approvalCommandClient(ce)
		if client == nil {
			return
		}
		if len(ce.Args) != 1 {
			ce.Reply("Usage: `$cmdprefix allow|deny|unallow|undeny <number>`")
			return
		}
		approvalID, err := strconv.ParseInt(ce.Args[0], 10, 64)
		if err != nil {
			ce.Reply("Invalid approval number: %s", format.SafeMarkdownCode(ce.Args[0]))
			return
		}
		if err = client.cleanupOldPendingPortalApprovals(ce.Ctx); err != nil {
			ce.Log.Err(err).Msg("Failed to clean old pending Telegram portal approvals")
			ce.Reply("Failed to clean old pending Telegram chats: %v", err)
			return
		}
		item, err := client.main.Store.Approval.GetByID(ce.Ctx, client.telegramUserID, approvalID)
		if err != nil {
			ce.Log.Err(err).Int64("approval_id", approvalID).Msg("Failed to fetch Telegram portal approval")
			ce.Reply("Failed to fetch approval item: %v", err)
			return
		} else if item == nil {
			ce.Reply("No Telegram approval item found with number %d.", approvalID)
			return
		}
		if !approvalStatusAllowed(item.Status, allowedSourceStatuses) {
			ce.Reply(
				"Approval item %d is currently %s, not %s. Run `$cmdprefix %s` to refresh the list.",
				approvalID, item.Status, approvalAllowedSourceStatusText(allowedSourceStatuses), item.Status,
			)
			return
		}
		if err = client.main.Store.Approval.SetStatus(ce.Ctx, client.telegramUserID, approvalID, status); err != nil {
			ce.Log.Err(err).Int64("approval_id", approvalID).Str("approval_status", string(status)).Msg("Failed to update Telegram portal approval")
			ce.Reply("Failed to update approval item: %v", err)
			return
		}
		if createPortal {
			portalKey := networkid.PortalKey{ID: item.PortalID, Receiver: item.PortalReceiver}
			res := client.main.Bridge.QueueRemoteEvent(client.userLogin, &simplevent.ChatResync{
				EventMeta: simplevent.EventMeta{
					Type:         bridgev2.RemoteEventChatResync,
					PortalKey:    portalKey,
					CreatePortal: true,
					LogContext: func(c zerolog.Context) zerolog.Context {
						return c.
							Int64("approval_id", approvalID).
							Str("approval_command", "allow")
					},
				},
				GetChatInfoFunc: client.GetChatInfo,
			})
			if err = resultToError(res); err != nil {
				ce.Log.Err(err).Int64("approval_id", approvalID).Msg("Failed to create approved Telegram portal")
				ce.Reply("Approved %s, but failed to create the Matrix room: %v", approvalDisplayName(*item), err)
				return
			}
			ce.Reply("Approved %s and requested Matrix room creation.", approvalDisplayName(*item))
		} else {
			if cleanupPortal && client.shouldDeleteApprovalPortal(status) {
				deleted, err := client.deleteApprovalPortal(ce, *item)
				if err != nil {
					ce.Log.Err(err).Int64("approval_id", approvalID).Msg("Failed to delete Telegram approval portal")
					ce.Reply("Moved %s to %s, but failed to delete the Matrix portal: %v", approvalDisplayName(*item), status, err)
					return
				} else if deleted {
					ce.Reply("Moved %s to %s and deleted the Matrix portal room.", approvalDisplayName(*item), status)
					return
				}
			}
			ce.Reply("Moved %s to %s.", approvalDisplayName(*item), status)
		}
	}
}

func (tc *TelegramClient) shouldDeleteApprovalPortal(status store.PortalApprovalStatus) bool {
	switch status {
	case store.PortalApprovalDenied:
		return tc.main.Config.PortalApproval.Cleanup.OnDeny.DeletePortal
	case store.PortalApprovalPending:
		return tc.main.Config.PortalApproval.Cleanup.OnUnallow.DeletePortal
	default:
		return false
	}
}

func (tc *TelegramClient) deleteApprovalPortal(ce *commands.Event, item store.PortalApproval) (bool, error) {
	portalKey := networkid.PortalKey{ID: item.PortalID, Receiver: item.PortalReceiver}
	portal, err := tc.main.Bridge.GetExistingPortalByKey(ce.Ctx, portalKey)
	if err != nil {
		return false, fmt.Errorf("failed to get Matrix portal: %w", err)
	} else if portal == nil {
		return false, nil
	}
	if err = tc.ensureApprovalPortalNotShared(ce, portalKey); err != nil {
		return false, err
	}

	roomID := portal.MXID
	if err = portal.Delete(ce.Ctx); err != nil {
		return false, fmt.Errorf("failed to delete bridge portal row: %w", err)
	}
	if roomID != "" {
		if err = ce.Bot.DeleteRoom(ce.Ctx, roomID, false); err != nil {
			return true, fmt.Errorf("failed to clean up Matrix room %s: %w", roomID, err)
		}
	}
	return true, nil
}

func (tc *TelegramClient) ensureApprovalPortalNotShared(ce *commands.Event, portalKey networkid.PortalKey) error {
	userPortals, err := tc.main.Bridge.DB.UserPortal.GetAllInPortal(ce.Ctx, portalKey)
	if err != nil {
		return fmt.Errorf("failed to check portal users: %w", err)
	}
	for _, userPortal := range userPortals {
		if userPortal.LoginID != tc.userLogin.ID {
			return fmt.Errorf("portal is also used by another login (%s), not deleting shared Matrix room", userPortal.LoginID)
		}
	}
	return nil
}

func approvalStatusAllowed(status store.PortalApprovalStatus, allowed []store.PortalApprovalStatus) bool {
	for _, allowedStatus := range allowed {
		if status == allowedStatus {
			return true
		}
	}
	return false
}

func approvalAllowedSourceStatusText(statuses []store.PortalApprovalStatus) string {
	if len(statuses) == 0 {
		return "any status"
	}
	parts := make([]string, len(statuses))
	for i, status := range statuses {
		parts[i] = string(status)
	}
	return strings.Join(parts, " or ")
}

func approvalStatusTitle(status store.PortalApprovalStatus) string {
	switch status {
	case store.PortalApprovalPending:
		return "Pending"
	case store.PortalApprovalAllowed:
		return "Allowed"
	case store.PortalApprovalDenied:
		return "Denied"
	default:
		return string(status)
	}
}

func approvalPeerTypeTitle(peerType string) string {
	switch peerType {
	case string(ids.PeerTypeUser):
		return "Private chats"
	case string(ids.PeerTypeChat):
		return "Groups"
	case "supergroup":
		return "Supergroups"
	case string(ids.PeerTypeChannel):
		return "Channels"
	default:
		return "Other"
	}
}

func writeApprovalSectionHeader(builder *strings.Builder, title string, first bool) {
	if first {
		fmt.Fprintf(builder, "\n**%s:**\n\n", format.EscapeMarkdown(title))
	} else {
		fmt.Fprintf(builder, "\n\n**%s:**\n\n", format.EscapeMarkdown(title))
	}
}

func approvalDisplayName(item store.PortalApproval) string {
	if item.Username != "" {
		return fmt.Sprintf("%s (@%s)", item.Title, item.Username)
	}
	return item.Title
}

func formatApprovalItem(item store.PortalApproval) string {
	var builder strings.Builder
	fmt.Fprintf(&builder, "%d\\. **%s**\n", item.ApprovalID, format.EscapeMarkdown(item.Title))
	if item.Username != "" {
		fmt.Fprintf(&builder, "   username: @%s\n", format.EscapeMarkdown(item.Username))
	} else {
		builder.WriteString("   username: -\n")
	}
	fmt.Fprintf(&builder, "   id: %s\n\n", format.EscapeMarkdown(approvalTelegramID(item)))
	return builder.String()
}

func approvalTelegramID(item store.PortalApproval) string {
	switch item.PeerType {
	case "supergroup", string(ids.PeerTypeChannel):
		return fmt.Sprintf("-100%d", item.EntityID)
	case string(ids.PeerTypeChat):
		return fmt.Sprintf("-%d", item.EntityID)
	default:
		return fmt.Sprintf("%d", item.EntityID)
	}
}

var cmdEmojiPack = &commands.FullHandler{
	Func:    fnEmojiPack,
	Name:    "emoji-pack",
	Aliases: []string{"pack", "sticker-pack", "emojipack", "stickerpack"},
	Help: commands.HelpMeta{
		Section:     commands.HelpSectionMisc,
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
			ce.Reply("You're not logged in")
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
