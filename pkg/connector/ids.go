package connector

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2/networkid"
)

func makeUserID(userID int64) networkid.UserID {
	return networkid.UserID(strconv.FormatInt(userID, 10))
}

func parseUserID(userID networkid.UserID) (int64, error) {
	return strconv.ParseInt(string(userID), 10, 64)
}

func makeUserLoginID(userID int64) networkid.UserLoginID {
	return networkid.UserLoginID(strconv.FormatInt(userID, 10))
}

func makeMessageID(messageID int) networkid.MessageID {
	return networkid.MessageID(strconv.Itoa(messageID))
}

type peerType string

const (
	peerTypeUser    peerType = "user"
	peerTypeChat    peerType = "chat"
	peerTypeChannel peerType = "channel"
)

func makePortalID(peer tg.PeerClass) networkid.PortalID {
	switch v := peer.(type) {
	case *tg.PeerUser:
		return networkid.PortalID(fmt.Sprintf("%s:%d", peerTypeUser, v.UserID))
	case *tg.PeerChat:
		return networkid.PortalID(fmt.Sprintf("%s:%d", peerTypeChat, v.ChatID))
	case *tg.PeerChannel:
		return networkid.PortalID(fmt.Sprintf("%s:%d", peerTypeChannel, v.ChannelID))
	default:
		panic(fmt.Errorf("unknown peer class type %T", v))
	}
}

func parsePortalID(portalID networkid.PortalID) (pt peerType, id int64, err error) {
	parts := strings.Split(string(portalID), ":")
	pt = peerType(parts[0])
	id, err = strconv.ParseInt(parts[1], 10, 64)
	return
}

func inputPeerForPortalID(portalID networkid.PortalID) (tg.InputPeerClass, error) {
	peerType, id, err := parsePortalID(portalID)
	if err != nil {
		return nil, err
	}
	switch peerType {
	case peerTypeUser:
		return &tg.InputPeerUser{UserID: id}, nil
	case peerTypeChat:
		return &tg.InputPeerChat{ChatID: id}, nil
	case peerTypeChannel:
		return &tg.InputPeerChannel{ChannelID: id}, nil
	default:
		panic("invalid peer type")
	}
}
