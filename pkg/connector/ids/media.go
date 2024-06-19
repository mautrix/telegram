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
// v|p|CCCCCCCC|TTTTTTTT
//
// v (int8) = binary encoding format version. Should be 0.
// p (byte) = the peer type of the Telegram chat ID
// CCCCCCCC (int64) = the Telegram chat ID (big endian)
// TTTTTTTT (int64) = the Telegram message ID (big endian)
type DirectMediaInfo struct {
	PeerType  PeerType
	ChatID    int64
	MessageID int64
}

func (m DirectMediaInfo) AsMediaID() (networkid.MediaID, error) {
	mediaID := []byte{
		0x00,                // Version
		m.PeerType.AsByte(), // Peer Type
	}
	mediaID = binary.BigEndian.AppendUint64(mediaID, uint64(m.ChatID))    // Telegram Chat ID
	mediaID = binary.BigEndian.AppendUint64(mediaID, uint64(m.MessageID)) // Telegram Message ID
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
	if len(mediaID) != 18 {
		err = fmt.Errorf("invalid media ID")
		return
	}
	fmt.Printf("%v\n", mediaID)
	info.PeerType, err = PeerTypeFromByte(mediaID[1])
	if err != nil {
		return
	}
	info.ChatID = int64(binary.BigEndian.Uint64(mediaID[2:]))
	info.MessageID = int64(binary.BigEndian.Uint64(mediaID[10:]))
	return
}
