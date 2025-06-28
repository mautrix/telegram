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

	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/gotd/crypto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/session"
)

func (tg *TelegramConnector) GetDBMetaTypes() database.MetaTypes {
	return database.MetaTypes{
		Ghost:     func() any { return &GhostMetadata{} },
		Portal:    func() any { return &PortalMetadata{} },
		Message:   func() any { return &MessageMetadata{} },
		Reaction:  nil,
		UserLogin: func() any { return &UserLoginMetadata{} },
	}
}

type GhostMetadata struct {
	IsPremium bool `json:"is_premium,omitempty"`
	IsBot     bool `json:"is_bot,omitempty"`
	IsChannel bool `json:"is_channel,omitempty"`
	IsContact bool `json:"is_contact,omitempty"`
	Blocked   bool `json:"blocked,omitempty"`
	Deleted   bool `json:"deleted,omitempty"`
}

type PortalMetadata struct {
	IsSuperGroup     bool     `json:"is_supergroup,omitempty"`
	ReadUpTo         int      `json:"read_up_to,omitempty"`
	MessagesTTL      int      `json:"messages_ttl,omitempty"`
	AllowedReactions []string `json:"allowed_reactions"`
}

func (pm *PortalMetadata) SetIsSuperGroup(isSupergroup bool) (changed bool) {
	changed = pm.IsSuperGroup != isSupergroup
	pm.IsSuperGroup = isSupergroup
	return changed
}

type MessageMetadata struct {
	ContentHash []byte              `json:"content_hash,omitempty"`
	ContentURI  id.ContentURIString `json:"content_uri,omitempty"`
}

type UserLoginMetadata struct {
	Phone     string           `json:"phone"`
	Session   UserLoginSession `json:"session"`
	TakeoutID int64            `json:"takeout_id,omitempty"`

	TakeoutDialogCrawlDone   bool               `json:"takeout_portal_crawl_done,omitempty"`
	TakeoutDialogCrawlCursor networkid.PortalID `json:"takeout_portal_crawl_cursor,omitempty"`

	PinnedDialogs []networkid.PortalID `json:"pinned_dialogs,omitempty"`

	PushEncryptionKey []byte `json:"push_encryption_key,omitempty"`
}

func (u *UserLoginMetadata) ResetOnLogout() {
	u.Session.AuthKey = nil
	u.TakeoutID = 0
	u.TakeoutDialogCrawlDone = false
	u.TakeoutDialogCrawlCursor = networkid.PortalID("")
	u.PushEncryptionKey = nil
}

type UserLoginSession struct {
	AuthKey       []byte `json:"auth_key,omitempty"`
	Datacenter    int    `json:"dc_id,omitempty"`
	ServerAddress string `json:"server_address,omitempty"`
	ServerPort    int    `json:"port,omitempty"`
	Salt          int64  `json:"salt,omitempty"`
}

func (u UserLoginSession) HasAuthKey() bool {
	return len(u.AuthKey) == 256
}

func (s *UserLoginSession) Load(_ context.Context) (*session.Data, error) {
	if !s.HasAuthKey() {
		return nil, session.ErrNotFound
	}
	keyID := crypto.Key(s.AuthKey).ID()
	return &session.Data{
		DC:        s.Datacenter,
		Addr:      s.ServerAddress,
		AuthKey:   s.AuthKey,
		AuthKeyID: keyID[:],
		Salt:      s.Salt,
	}, nil
}

func (s *UserLoginSession) Save(ctx context.Context, data *session.Data) error {
	s.Datacenter = data.DC
	s.ServerAddress = data.Addr
	s.AuthKey = data.AuthKey
	s.Salt = data.Salt
	// TODO save UserLogin to database?
	return nil
}
