package ids

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2/networkid"
)

func MakeUserID(userID int64) networkid.UserID {
	return networkid.UserID(strconv.FormatInt(userID, 10))
}

func ParseUserID(userID networkid.UserID) (int64, error) {
	return strconv.ParseInt(string(userID), 10, 64)
}

func MakeUserLoginID(userID int64) networkid.UserLoginID {
	return networkid.UserLoginID(strconv.FormatInt(userID, 10))
}

func MakeMessageID(messageID int) networkid.MessageID {
	return networkid.MessageID(strconv.Itoa(messageID))
}

type PeerType string

const (
	PeerTypeUser    PeerType = "user"
	PeerTypeChat    PeerType = "chat"
	PeerTypeChannel PeerType = "channel"
)

func PeerTypeFromByte(pt byte) (PeerType, error) {
	switch pt {
	case 0x01:
		return PeerTypeUser, nil
	case 0x02:
		return PeerTypeChat, nil
	case 0x03:
		return PeerTypeChannel, nil
	default:
		return "", fmt.Errorf("unknown peer type %d", pt)
	}
}

func (pt PeerType) AsByte() byte {
	switch pt {
	case PeerTypeUser:
		return 0x01
	case PeerTypeChat:
		return 0x02
	case PeerTypeChannel:
		return 0x03
	default:
		panic(fmt.Errorf("unknown peer type %s", pt))
	}
}

func (pt PeerType) AsPortalKey(chatID int64) networkid.PortalKey {
	return networkid.PortalKey{ID: networkid.PortalID(fmt.Sprintf("%s:%d", pt, chatID))}
}

func MakePortalID(peer tg.PeerClass) networkid.PortalKey {
	switch v := peer.(type) {
	case *tg.PeerUser:
		return networkid.PortalKey{ID: networkid.PortalID(fmt.Sprintf("%s:%d", PeerTypeUser, v.UserID))}
	case *tg.PeerChat:
		return networkid.PortalKey{ID: networkid.PortalID(fmt.Sprintf("%s:%d", PeerTypeChat, v.ChatID))}
	case *tg.PeerChannel:
		return networkid.PortalKey{ID: networkid.PortalID(fmt.Sprintf("%s:%d", PeerTypeChannel, v.ChannelID))}
	default:
		panic(fmt.Errorf("unknown peer class type %T", v))
	}
}

func ParsePortalID(portalID networkid.PortalID) (pt PeerType, id int64, err error) {
	parts := strings.Split(string(portalID), ":")
	pt = PeerType(parts[0])
	id, err = strconv.ParseInt(parts[1], 10, 64)
	return
}

func InputPeerForPortalID(portalID networkid.PortalID) (tg.InputPeerClass, error) {
	peerType, id, err := ParsePortalID(portalID)
	if err != nil {
		return nil, err
	}
	switch peerType {
	case PeerTypeUser:
		return &tg.InputPeerUser{UserID: id}, nil
	case PeerTypeChat:
		return &tg.InputPeerChat{ChatID: id}, nil
	case PeerTypeChannel:
		return &tg.InputPeerChannel{ChannelID: id}, nil
	default:
		panic("invalid peer type")
	}
}

func MakeAvatarID(photoID int64) networkid.AvatarID {
	return networkid.AvatarID(strconv.FormatInt(photoID, 10))
}
