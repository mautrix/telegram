package connector

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

func (t *TelegramClient) getResolveIdentifierResponseForUserID(ctx context.Context, user tg.UserClass) (*bridgev2.ResolveIdentifierResponse, error) {
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
		} else if user, err := t.getSingleUser(ctx, userID); err != nil {
			return nil, fmt.Errorf("failed to get user with ID %d: %w", userID, err)
		} else {
			return t.getResolveIdentifierResponseForUserID(ctx, user)
		}
	} else if userID, err := strconv.ParseInt(identifier, 10, 64); err == nil {
		// This is an integer, try and parse it as a Telegram User ID
		if user, err := t.getSingleUser(ctx, userID); err != nil {
			return nil, fmt.Errorf("failed to get user with ID %d: %w", userID, err)
		} else {
			return t.getResolveIdentifierResponseForUserID(ctx, user)
		}
	} else if match := usernameRe.FindStringSubmatch(identifier); match != nil && !strings.Contains(identifier, "__") {
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
				return t.getResolveIdentifierResponseForUserID(ctx, user)
			}
		}
		return nil, fmt.Errorf("peer user not found in contact resolved response")
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
		peer, ok := p.(*tg.PeerUser)
		if !ok {
			return nil
		}
		if user, ok := users[peer.GetUserID()]; ok {
			if r, err := t.getResolveIdentifierResponseForUserID(ctx, user); err != nil {
				return err
			} else {
				resp = append(resp, r)
			}
		} else {
			return fmt.Errorf("peer user not found in contact search response")
		}
		return nil
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
