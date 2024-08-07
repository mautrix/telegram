package ids

import (
	"encoding/binary"
	"fmt"

	"maunium.net/go/mautrix/bridgev2/networkid"
)

// DirectMediaInfo is the information that is encoded in the media ID when
// using direct media.
//
// The format of the media ID is as follows (each character represents a single
// byte, |'s added for clarity):
//
// v|p|cccccccc|rrrrrrrr|mmmmmmmm|T|MMMMMMMM
//
// v (int8) = binary encoding format version. Should be 0.
// p (byte) = the peer type of the Telegram chat ID
// cccccccc (int64) = the Telegram chat ID (big endian)
// rrrrrrrr (int64) = the receiver ID (big endian)
// mmmmmmmm (int64) = the Telegram message ID (big endian)
// MMMMMMMM (int64) = the Telegram media ID (big endian)
// T (byte) = 0 or 1 depending on whether it's a thumbnail
type DirectMediaInfo struct {
	PeerType        PeerType
	ChatID          int64
	ReceiverID      int64
	MessageID       int64
	TelegramMediaID int64
	Thumbnail       bool
}

func (m DirectMediaInfo) AsMediaID() (networkid.MediaID, error) {
	mediaID := []byte{
		0x00,                // Version
		m.PeerType.AsByte(), // Peer Type
	}
	mediaID = binary.BigEndian.AppendUint64(mediaID, uint64(m.ChatID))          // Telegram Chat ID
	mediaID = binary.BigEndian.AppendUint64(mediaID, uint64(m.ReceiverID))      // Telegram Chat ID
	mediaID = binary.BigEndian.AppendUint64(mediaID, uint64(m.MessageID))       // Telegram Message ID
	mediaID = binary.BigEndian.AppendUint64(mediaID, uint64(m.TelegramMediaID)) // Telegram Media ID
	if m.Thumbnail {
		mediaID = append(mediaID, 0x01)
	} else {
		mediaID = append(mediaID, 0x00)
	}
	return mediaID, nil
}

func ParseDirectMediaInfo(mediaID networkid.MediaID) (info DirectMediaInfo, err error) {
	if len(mediaID) == 0 {
		err = fmt.Errorf("empty media ID")
		return
	}
	if mediaID[0] != 0x00 {
		err = fmt.Errorf("invalid version %d", mediaID[0])
		return
	}
	if len(mediaID) != 35 {
		err = fmt.Errorf("invalid media ID")
		return
	}

	info.PeerType, err = PeerTypeFromByte(mediaID[1])
	if err != nil {
		return
	}
	info.ChatID = int64(binary.BigEndian.Uint64(mediaID[2:]))
	info.ReceiverID = int64(binary.BigEndian.Uint64(mediaID[10:]))
	info.MessageID = int64(binary.BigEndian.Uint64(mediaID[18:]))
	info.TelegramMediaID = int64(binary.BigEndian.Uint64(mediaID[26:]))
	info.Thumbnail = mediaID[34] == 1
	return
}
