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

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

func (t *TelegramClient) makePortalKeyFromPeer(peer tg.PeerClass) networkid.PortalKey {
	key := ids.InternalMakePortalKey(peer, t.loginID)
	if t.main.Bridge.Config.SplitPortals {
		key.Receiver = t.userLogin.ID
	}
	return key
}

func (t *TelegramClient) makePortalKeyFromID(peerType ids.PeerType, chatID int64) networkid.PortalKey {
	key := peerType.InternalAsPortalKey(chatID, t.loginID)
	if t.main.Bridge.Config.SplitPortals {
		key.Receiver = t.userLogin.ID
	}
	return key
}
