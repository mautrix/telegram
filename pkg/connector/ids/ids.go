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

package ids

import (
	"fmt"
	"strconv"
	"strings"

	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func MakeUserID(userID int64) networkid.UserID {
	return networkid.UserID(strconv.FormatInt(userID, 10))
}

func MakeChannelUserID(channelID int64) networkid.UserID {
	return networkid.UserID("channel-" + strconv.FormatInt(channelID, 10))
}

func ParseUserID(userID networkid.UserID) (PeerType, int64, error) {
	peerType := PeerTypeUser
	rawUserID := string(userID)
	if strings.HasPrefix(string(userID), "channel-") {
		peerType = PeerTypeChannel
		rawUserID = strings.TrimPrefix(rawUserID, "channel-")
	}
	id, err := strconv.ParseInt(rawUserID, 10, 64)
	return peerType, id, err
}

func ParseUserLoginID(userID networkid.UserLoginID) (int64, error) {
	return strconv.ParseInt(string(userID), 10, 64)
}

func MakeUserLoginID(userID int64) networkid.UserLoginID {
	return networkid.UserLoginID(strconv.FormatInt(userID, 10))
}

func GetMessageIDFromMessage(message tg.MessageClass) networkid.MessageID {
	var peer tg.PeerClass
	switch typedMsg := message.(type) {
	case *tg.MessageEmpty:
		peer, _ = typedMsg.GetPeerID()
	case *tg.Message:
		peer = typedMsg.GetPeerID()
	case *tg.MessageService:
		peer = typedMsg.GetPeerID()
	default:
		panic(fmt.Sprintf("unexpected message type %T", message))
	}
	return MakeMessageID(peer, message.GetID())
}

func MakeMessageID(rawChatID any, messageID int) networkid.MessageID {
	var channelID int64
	switch typedChatID := rawChatID.(type) {
	case networkid.PortalKey:
		peerType, entityID, _ := ParsePortalID(typedChatID.ID)
		if peerType == PeerTypeChannel {
			channelID = entityID
		}
	case *tg.PeerChannel:
		channelID = typedChatID.ChannelID
	case int64:
		channelID = typedChatID
	case *tg.PeerUser, *tg.PeerChat:
		// No channel ID
	case nil:
		// Also no channel ID
	default:
		panic(fmt.Sprintf("unexpected chat ID type %T", rawChatID))
	}
	if channelID != 0 {
		return networkid.MessageID(fmt.Sprintf("%d.%d", channelID, messageID))
	}
	return networkid.MessageID(fmt.Sprintf("%d", messageID))
}

func MakePaginationCursorID(messageID int) networkid.PaginationCursor {
	return networkid.PaginationCursor(strconv.Itoa(messageID))
}

func ParseMessageID(networkID networkid.MessageID) (channelID int64, messageID int, err error) {
	parts := strings.Split(string(networkID), ".")
	if len(parts) == 1 {
		messageID, err = strconv.Atoi(parts[0])
	} else if len(parts) == 2 {
		channelID, err = strconv.ParseInt(parts[0], 10, 64)
		if err != nil {
			err = fmt.Errorf("failed to parse chat ID: %w", err)
			return
		}
		messageID, err = strconv.Atoi(parts[1])
	} else {
		err = fmt.Errorf("invalid number of parts in message ID")
	}
	return
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

func (pt PeerType) InternalAsPortalKey(chatID int64, receiver networkid.UserLoginID) networkid.PortalKey {
	portalKey := networkid.PortalKey{
		ID: networkid.PortalID(fmt.Sprintf("%s:%d", pt, chatID)),
	}
	if pt == PeerTypeUser || pt == PeerTypeChat {
		portalKey.Receiver = receiver
	}
	return portalKey
}

func GetChatID(peer tg.PeerClass) int64 {
	switch v := peer.(type) {
	case *tg.PeerUser:
		return v.UserID
	case *tg.PeerChat:
		return v.ChatID
	case *tg.PeerChannel:
		return v.ChannelID
	default:
		panic(fmt.Errorf("unknown peer class type %T", v))
	}
}

func InternalMakePortalKey(peer tg.PeerClass, receiver networkid.UserLoginID) networkid.PortalKey {
	switch v := peer.(type) {
	case *tg.PeerUser:
		return networkid.PortalKey{
			ID:       networkid.PortalID(fmt.Sprintf("%s:%d", PeerTypeUser, v.UserID)),
			Receiver: receiver,
		}
	case *tg.PeerChat:
		return networkid.PortalKey{
			ID:       networkid.PortalID(fmt.Sprintf("%s:%d", PeerTypeChat, v.ChatID)),
			Receiver: receiver,
		}
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

func MakeAvatarID(photoID int64) networkid.AvatarID {
	return networkid.AvatarID(strconv.FormatInt(photoID, 10))
}

func MakeEmojiIDFromDocumentID(documentID int64) networkid.EmojiID {
	return networkid.EmojiID(strconv.FormatInt(documentID, 10))
}

func MakeEmojiIDFromEmoticon(emoji string) networkid.EmojiID {
	return networkid.EmojiID(emoji)
}

func isNumbers(s string) bool {
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func ParseEmojiID(emojiID networkid.EmojiID) (documentID int64, emoji string, err error) {
	if isNumbers(string(emojiID)) {
		documentID, err = strconv.ParseInt(string(emojiID), 10, 64)
	} else {
		emoji = string(emojiID)
	}
	return
}
