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
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func (tc *TelegramClient) makePortalKeyFromPeer(peer tg.PeerClass, topicID int) networkid.PortalKey {
	key := ids.InternalPeerToPortalKey(peer, topicID, tc.loginID)
	if tc.main.Bridge.Config.SplitPortals {
		key.Receiver = tc.userLogin.ID
	}
	return key
}

func (tc *TelegramClient) makePortalKeyFromID(peerType ids.PeerType, chatID int64, topicID int) networkid.PortalKey {
	key := ids.InternalMakePortalKey(peerType, chatID, topicID, tc.loginID)
	if tc.main.Bridge.Config.SplitPortals {
		key.Receiver = tc.userLogin.ID
	}
	return key
}
