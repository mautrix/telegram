package connector

import (
	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2/networkid"

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
