package connector

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/gotd/td/telegram/query/hasher"
	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

func (t *TelegramClient) getResolveIdentifierResponseForUser(ctx context.Context, user tg.UserClass) (*bridgev2.ResolveIdentifierResponse, error) {
	networkUserID := ids.MakeUserID(user.GetID())
	if userInfo, err := t.getUserInfoFromTelegramUser(ctx, user); err != nil {
		return nil, fmt.Errorf("failed to get user info: %w", err)
	} else if ghost, err := t.main.Bridge.GetGhostByID(ctx, networkUserID); err != nil {
		return nil, fmt.Errorf("failed to get ghost: %w", err)
	} else {
		return &bridgev2.ResolveIdentifierResponse{
			Ghost:    ghost,
			UserID:   networkUserID,
			UserInfo: userInfo,
			Chat: &bridgev2.CreateChatResponse{
				PortalKey: ids.PeerTypeUser.AsPortalKey(user.GetID(), t.loginID),
			},
		}, nil
	}
}

func (t *TelegramClient) getResolveIdentifierResponseForUserID(ctx context.Context, userID int64) (resp *bridgev2.ResolveIdentifierResponse, err error) {
	networkUserID := ids.MakeUserID(userID)
	resp = &bridgev2.ResolveIdentifierResponse{
		UserID: networkUserID,
		Chat: &bridgev2.CreateChatResponse{
			PortalKey: ids.PeerTypeUser.AsPortalKey(userID, t.loginID),
		},
	}
	resp.Ghost, err = t.main.Bridge.GetExistingGhostByID(ctx, networkUserID)
	if err != nil {
		// Try to fetch the user from Telegram
		if user, err := t.getSingleUser(ctx, userID); err != nil {
			return nil, fmt.Errorf("failed to get user with ID %d: %w", userID, err)
		} else {
			return t.getResolveIdentifierResponseForUser(ctx, user)
		}
	}
	return
}

// Parses usernames with or without the @ sign in front of the username.
// This verifies the following restrictions:
// - Usernames must be at least 5 characters long
// - Usernames must be at most 32 characters long
// - Usernames must start with a letter
// - Usernames must contain only letters, numbers, and underscores
// - Usernames cannot end with an underscore
var usernameRe = regexp.MustCompile(`^@?([a-zA-Z](?:\w{3,30})[a-zA-Z\d])$`)

func (t *TelegramClient) ResolveIdentifier(ctx context.Context, identifier string, createChat bool) (*bridgev2.ResolveIdentifierResponse, error) {
	if len(identifier) == 0 {
		return nil, fmt.Errorf("empty identifier")
	}

	if identifier[0] == '+' {
		normalized := strings.TrimPrefix(identifier, "+")
		if userID, err := t.ScopedStore.GetUserIDByPhoneNumber(ctx, normalized); err != nil {
			return nil, fmt.Errorf("failed to get user ID by phone number: %w", err)
		} else if userID == 0 {
			return nil, fmt.Errorf("no user found with phone number '%s'", normalized)
		} else {
			return t.getResolveIdentifierResponseForUserID(ctx, userID)
		}
	} else if userID, err := strconv.ParseInt(identifier, 10, 64); err == nil {
		// This is an integer, try and parse it as a Telegram User ID
		return t.getResolveIdentifierResponseForUserID(ctx, userID)
	} else if match := usernameRe.FindStringSubmatch(identifier); match != nil && !strings.Contains(identifier, "__") {
		// This is a username
		userID, err := t.ScopedStore.GetUserIDByUsername(ctx, match[1])
		if err == nil || userID != 0 {
			// We know this username.
			return t.getResolveIdentifierResponseForUserID(ctx, userID)
		} else {
			// We don't know this username, try to resolve the username from
			// Telegram.
			resolved, err := APICallWithUpdates(ctx, t, func() (*tg.ContactsResolvedPeer, error) {
				return t.client.API().ContactsResolveUsername(ctx, match[1])
			})
			if err != nil {
				if tg.IsUsernameNotOccupied(err) {
					return nil, fmt.Errorf("no user found with username '%s'", match[1])
				} else {
					return nil, fmt.Errorf("failed to resolve username: %w", err)
				}
			}
			peer, ok := resolved.GetPeer().(*tg.PeerUser)
			if !ok {
				return nil, fmt.Errorf("unexpected peer type: %T", resolved.GetPeer())
			}
			for _, user := range resolved.GetUsers() {
				if user.GetID() == peer.GetUserID() {
					return t.getResolveIdentifierResponseForUser(ctx, user)
				}
			}
			return nil, fmt.Errorf("peer user not found in contact resolved response")
		}
	} else {
		return nil, fmt.Errorf("invalid identifier: %s (must be a phone number, username, or Telegram user ID)", identifier)
	}
}

func (t *TelegramClient) SearchUsers(ctx context.Context, query string) (resp []*bridgev2.ResolveIdentifierResponse, err error) {
	contactsFound, err := APICallWithUpdates(ctx, t, func() (*tg.ContactsFound, error) {
		return t.client.API().ContactsSearch(ctx, &tg.ContactsSearchRequest{Q: query})
	})
	if err != nil {
		return nil, err
	}
	users := map[int64]tg.UserClass{}
	for _, user := range contactsFound.GetUsers() {
		users[user.GetID()] = user
	}

	addResult := func(p tg.PeerClass) error {
		if peer, ok := p.(*tg.PeerUser); !ok {
			return nil
		} else if user, ok := users[peer.GetUserID()]; ok {
			if r, err := t.getResolveIdentifierResponseForUser(ctx, user); err != nil {
				return err
			} else {
				resp = append(resp, r)
			}
			return nil
		} else {
			return fmt.Errorf("peer user not found in contact search response")
		}
	}

	for _, p := range contactsFound.MyResults {
		if err := addResult(p); err != nil {
			return nil, err
		}
	}
	for _, p := range contactsFound.Results {
		if err := addResult(p); err != nil {
			return nil, err
		}
	}
	return resp, nil
}

func (t *TelegramClient) GetContactList(ctx context.Context) (resp []*bridgev2.ResolveIdentifierResponse, err error) {
	contacts, err := APICallWithOnlyUserUpdates(ctx, t, func() (*tg.ContactsContacts, error) {
		c, err := t.client.API().ContactsGetContacts(ctx, t.cachedContactsHash)
		if err != nil {
			return nil, err
		}
		if c.TypeID() == tg.ContactsContactsTypeID {
			t.cachedContacts = c.(*tg.ContactsContacts)
			var h hasher.Hasher
			for _, contact := range t.cachedContacts.Contacts {
				h.Update(uint32(contact.UserID))
			}
			t.cachedContactsHash = h.Sum()
		} else if c.TypeID() != tg.ContactsContactsNotModifiedTypeID {
			return nil, fmt.Errorf("unexpected contacts type: %T", c)
		}
		return t.cachedContacts, nil
	})
	if err != nil {
		return nil, err
	}
	users := map[int64]tg.UserClass{}
	for _, user := range contacts.GetUsers() {
		users[user.GetID()] = user
	}

	for _, contact := range contacts.Contacts {
		if user, ok := users[contact.UserID]; ok {
			if r, err := t.getResolveIdentifierResponseForUser(ctx, user); err != nil {
				return nil, err
			} else {
				resp = append(resp, r)
			}
		} else {
			return nil, fmt.Errorf("contact user not found in contact list response")
		}
	}
	return resp, nil
}

// TODO support channels
func (t *TelegramClient) CreateGroup(ctx context.Context, name string, users ...networkid.UserID) (*bridgev2.CreateChatResponse, error) {
	if len(users) == 0 {
		return nil, fmt.Errorf("no users provided")
	} else if len(users) > 200 {
		return nil, fmt.Errorf("too many users provided: %d (max 200)", len(users))
	}
	req := tg.MessagesCreateChatRequest{
		Title: name,
	}
	for _, networkUserID := range users {
		if userID, err := ids.ParseUserID(networkUserID); err != nil {
			return nil, fmt.Errorf("failed to parse user ID: %w", err)
		} else if inputUser, err := t.getInputUser(ctx, userID); err != nil {
			return nil, fmt.Errorf("failed to get input user: %w", err)
		} else {
			req.Users = append(req.Users, inputUser)
		}
	}
	invitedUsers, err := t.client.API().MessagesCreateChat(ctx, &req)
	if err != nil {
		return nil, fmt.Errorf("failed to create chat: %w", err)
	}
	invited, ok := invitedUsers.Updates.(interface {
		GetChats() (value []tg.ChatClass)
	})
	if !ok {
		return nil, fmt.Errorf("unexpected response type: %T", invitedUsers.Updates)
	}

	// TODO notify about users that couldn't be invited

	if chats := invited.GetChats(); len(chats) != 1 {
		return nil, fmt.Errorf("unexpected number of chats: %d", len(chats))
	} else if chat, ok := chats[0].(*tg.Chat); !ok {
		return nil, fmt.Errorf("unexpected chat type: %T", chats[0])
	} else {
		return &bridgev2.CreateChatResponse{
			PortalKey: ids.PeerTypeChat.AsPortalKey(chat.ID, t.loginID),
		}, nil
	}
}
