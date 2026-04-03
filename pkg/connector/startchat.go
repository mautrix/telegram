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
	"context"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/util/ptr"
	"maunium.net/go/mautrix/bridgev2"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/query/hasher"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

var (
	_ bridgev2.IdentifierResolvingNetworkAPI = (*TelegramClient)(nil)
	_ bridgev2.ContactListingNetworkAPI      = (*TelegramClient)(nil)
	_ bridgev2.UserSearchingNetworkAPI       = (*TelegramClient)(nil)
	_ bridgev2.GroupCreatingNetworkAPI       = (*TelegramClient)(nil)
)

func (t *TelegramClient) resolveUser(ctx context.Context, user tg.UserClass) (*bridgev2.ResolveIdentifierResponse, error) {
	networkUserID := ids.MakeUserID(user.GetID())
	if ghost, err := t.main.Bridge.GetGhostByID(ctx, networkUserID); err != nil {
		return nil, fmt.Errorf("failed to get ghost: %w", err)
	} else if userInfo, err := t.wrapUserInfo(ctx, user, ghost); err != nil {
		return nil, fmt.Errorf("failed to get user info: %w", err)
	} else {
		return t.makeResolveIdentifierResponse(ghost, user, userInfo), nil
	}
}

func (t *TelegramClient) makeResolveIdentifierResponse(ghost *bridgev2.Ghost, user tg.UserClass, info *bridgev2.UserInfo) *bridgev2.ResolveIdentifierResponse {
	return &bridgev2.ResolveIdentifierResponse{
		Ghost:    ghost,
		UserID:   ids.MakeUserID(user.GetID()),
		UserInfo: info,
		Chat: &bridgev2.CreateChatResponse{
			PortalKey: t.makePortalKeyFromID(ids.PeerTypeUser, user.GetID(), 0),
		},
	}
}

func (t *TelegramClient) resolveUserID(ctx context.Context, userID int64) (resp *bridgev2.ResolveIdentifierResponse, err error) {
	_, err = t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeUser, userID)
	if errors.Is(err, store.ErrNoAccessHash) {
		username, usernameErr := t.main.Store.Username.Get(ctx, ids.PeerTypeUser, userID)
		if usernameErr != nil {
			return nil, fmt.Errorf("failed to get username after missing access hash: %w", usernameErr)
		} else if username != "" {
			zerolog.Ctx(ctx).Debug().
				Str("target_username", username).
				Int64("target_user_id", userID).
				Msg("Access hash not found for user ID, trying to look up username")
			return t.resolveUsername(ctx, username, userID)
		}
		return nil, fmt.Errorf("%w: %w", bridgev2.ErrResolveIdentifierTryNext, err)
	} else if err != nil {
		return nil, fmt.Errorf("failed to get access hash from store: %w", err)
	}
	networkUserID := ids.MakeUserID(userID)
	resp = &bridgev2.ResolveIdentifierResponse{
		UserID: networkUserID,
		Chat: &bridgev2.CreateChatResponse{
			PortalKey: t.makePortalKeyFromID(ids.PeerTypeUser, userID, 0),
		},
	}
	resp.Ghost, err = t.main.Bridge.GetExistingGhostByID(ctx, networkUserID)
	if err != nil {
		return nil, fmt.Errorf("failed to get ghost: %w", err)
	} else if resp.Ghost == nil || resp.Ghost.Name == "" {
		// Try to fetch the user from Telegram
		if user, err := t.getSingleUser(ctx, userID); err != nil {
			return nil, fmt.Errorf("failed to get user with ID %d: %w", userID, err)
		} else if user.TypeID() != tg.UserTypeID {
			return nil, fmt.Errorf("unexpected user type: %T", user)
		} else if userInfo, err := t.updateGhost(ctx, userID, user.(*tg.User)); err != nil {
			return nil, fmt.Errorf("failed to update ghost: %w", err)
		} else {
			if resp.Ghost == nil {
				resp.Ghost, _ = t.main.Bridge.GetExistingGhostByID(ctx, networkUserID)
			}
			return t.makeResolveIdentifierResponse(resp.Ghost, user, userInfo), nil
		}
	}
	return
}

func (t *TelegramClient) resolveUsername(ctx context.Context, username string, expectedID int64) (*bridgev2.ResolveIdentifierResponse, error) {
	resolved, err := APICallWithUpdates(ctx, t, func() (*tg.ContactsResolvedPeer, error) {
		return t.client.API().ContactsResolveUsername(ctx, &tg.ContactsResolveUsernameRequest{
			Username: username,
		})
	})
	if tg.IsUsernameNotOccupied(err) {
		if expectedID != 0 {
			err = t.main.Store.Username.Delete(ctx, username)
			if err != nil {
				zerolog.Ctx(ctx).Warn().Err(err).Str("username", username).
					Msg("Failed to delete stale username mapping")
			}
			return nil, fmt.Errorf("%w: resolving %s didn't return a result (wanted %d)", bridgev2.ErrResolveIdentifierTryNext, username, expectedID)
		}
		return nil, nil
	} else if err != nil {
		return nil, fmt.Errorf("failed to resolve username: %w", err)
	}
	peer, ok := resolved.GetPeer().(*tg.PeerUser)
	if !ok {
		return nil, fmt.Errorf("unexpected peer type: %T", resolved.GetPeer())
	}
	if expectedID != 0 && peer.GetUserID() != expectedID {
		return nil, fmt.Errorf("%w: resolving %s returned %d instead of %d", bridgev2.ErrResolveIdentifierTryNext, username, peer.GetUserID(), expectedID)
	}
	for _, user := range resolved.GetUsers() {
		if user.GetID() == peer.GetUserID() {
			return t.resolveUser(ctx, user)
		}
	}
	return nil, fmt.Errorf("peer user not found in contact resolved response")
}

// Parses user links or usernames with or without the @ sign in front of the username.
// This verifies the following restrictions:
// - Usernames must be at least 5 characters long
// - Usernames must be at most 32 characters long
// - Usernames must start with a letter
// - Usernames must contain only letters, numbers, and underscores
// - Usernames cannot end with an underscore
// TODO some usernames are shorter, figure out actual limits
// (some bots like @pic and @gif have 3 characters, fragment might allow 4 characters)
var usernameRe = regexp.MustCompile(`^(?:(?:https?://)?t(?:elegram)?\.(?:me|dog)/|tg:/{0,2}resolve\?domain=|@)?([a-zA-Z]\w{3,30}[a-zA-Z\d])$`)

func (t *TelegramClient) ResolveIdentifier(ctx context.Context, identifier string, createChat bool) (*bridgev2.ResolveIdentifierResponse, error) {
	log := zerolog.Ctx(ctx).With().Str("identifier", identifier).Logger()
	log.Debug().Msg("Resolving identifier")

	if len(identifier) == 0 {
		return nil, fmt.Errorf("empty identifier")
	}

	if identifier[0] == '+' {
		normalized := strings.TrimPrefix(identifier, "+")
		if userID, err := t.main.Store.PhoneNumber.GetUserID(ctx, normalized); err != nil {
			return nil, fmt.Errorf("failed to get user ID by phone number: %w", err)
		} else if userID == 0 {
			log.Info().Msg("Phone number not found in database")
			return nil, nil
		} else {
			return t.resolveUserID(ctx, userID)
		}
	} else if userID, err := strconv.ParseInt(identifier, 10, 64); err == nil && userID > 0 {
		// This is an integer, try and parse it as a Telegram User ID
		return t.resolveUserID(ctx, userID)
	} else if match := usernameRe.FindStringSubmatch(identifier); match != nil && !strings.Contains(identifier, "__") {
		// This is a username
		entityType, userID, err := t.main.Store.Username.GetEntityID(ctx, match[1])
		if entityType == ids.PeerTypeUser && (err == nil || userID != 0) {
			// We know this username.
			resp, err := t.resolveUserID(ctx, userID)
			if err == nil || !errors.Is(err, store.ErrNoAccessHash) {
				return resp, err
			}
		}
		return t.resolveUsername(ctx, match[1], 0)
	}
	return nil, fmt.Errorf("invalid identifier: %q (must be a phone number, username, or Telegram user ID)", identifier)
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
			if r, err := t.resolveUser(ctx, user); err != nil {
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
	t.contactsLock.Lock()
	defer t.contactsLock.Unlock()
	var contacts *tg.ContactsContacts
	if time.Since(t.lastContactReq) > 10*time.Minute {
		contacts, err = APICallWithOnlyUserUpdates(ctx, t, func() (*tg.ContactsContacts, error) {
			c, err := t.client.API().ContactsGetContacts(ctx, t.cachedContactsHash)
			if err != nil {
				return nil, err
			}
			switch typedResp := c.(type) {
			case *tg.ContactsContacts:
				t.cachedContacts = typedResp
				var h hasher.Hasher
				for _, contact := range t.cachedContacts.Contacts {
					h.Update(uint32(contact.UserID))
				}
				t.cachedContactsHash = h.Sum()
			case *tg.ContactsContactsNotModified:
				// No changes
			default:
				return nil, fmt.Errorf("unexpected contacts type: %T", c)
			}
			return t.cachedContacts, nil
		})
		if err != nil {
			return nil, err
		}
		t.lastContactReq = time.Now()
	} else {
		contacts = t.cachedContacts
	}
	users := map[int64]tg.UserClass{}
	for _, user := range contacts.GetUsers() {
		users[user.GetID()] = user
	}

	for _, contact := range contacts.Contacts {
		if user, ok := users[contact.UserID]; ok {
			if r, err := t.resolveUser(ctx, user); err != nil {
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
func (t *TelegramClient) CreateGroup(ctx context.Context, params *bridgev2.GroupCreateParams) (*bridgev2.CreateChatResponse, error) {
	req := tg.MessagesCreateChatRequest{
		Title: ptr.Val(params.Name).Name,
	}
	for _, networkUserID := range params.Participants {
		if peerType, userID, err := ids.ParseUserID(networkUserID); err != nil {
			return nil, fmt.Errorf("failed to parse user ID: %w", err)
		} else if peerType != ids.PeerTypeUser {
			return nil, fmt.Errorf("unexpected peer type: %s", peerType)
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
		portalKey := t.makePortalKeyFromID(ids.PeerTypeChat, chat.ID, 0)
		if params.RoomID != "" {
			portal, err := t.main.Bridge.GetPortalByKey(ctx, portalKey)
			if err != nil {
				return nil, err
			}
			err = portal.UpdateMatrixRoomID(ctx, params.RoomID, bridgev2.UpdateMatrixRoomIDParams{
				SyncDBMetadata: func() {
					portal.Name = req.Title
					portal.NameSet = true
				},
				OverwriteOldPortal: true,
				TombstoneOldRoom:   true,
				DeleteOldRoom:      true,
				ChatInfoSource:     t.userLogin,
			})
			if err != nil {
				return nil, err
			}
		}
		return &bridgev2.CreateChatResponse{
			PortalKey: portalKey,
		}, nil
	}
}
