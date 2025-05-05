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
	"bytes"
	"crypto/sha256"
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
// cccccccc (int64) = the Telegram peer ID (big endian)
// rrrrrrrr (int64) = the Telegram user ID (big endian)
// mmmmmmmm (int64) = the Telegram message ID (big endian)
// MMMMMMMM (int64) = the Telegram photo/file/document ID (big endian)
// T (byte) = 0 or 1 depending on whether it's a thumbnail
type DirectMediaInfo struct {
	// Type of PeerID
	PeerType PeerType

	// Peer ID, may be channel, chat or user
	PeerID int64

	// Telegram user ID of the client that downloads this media
	UserID int64

	// Telegram message ID if related to a message
	MessageID int64

	// Telegram photo/file/document ID, depends on PeerType
	ID int64

	// Is this a thumbnail?
	Thumbnail bool
}

func (m DirectMediaInfo) AsMediaID() (networkid.MediaID, error) {
	var mediaID networkid.MediaID
	buf := &bytes.Buffer{}

	// version byte
	if err := binary.Write(buf, binary.BigEndian, byte(0)); err != nil {
		return mediaID, err
	}

	// v0
	if err := binary.Write(buf, binary.BigEndian, m.PeerType.AsByte()); err != nil {
		return mediaID, err
	} else if err := binary.Write(buf, binary.BigEndian, m.PeerID); err != nil {
		return mediaID, err
	} else if err := binary.Write(buf, binary.BigEndian, m.UserID); err != nil {
		return mediaID, err
	} else if err := binary.Write(buf, binary.BigEndian, m.MessageID); err != nil {
		return mediaID, err
	} else if err := binary.Write(buf, binary.BigEndian, m.ID); err != nil {
		return mediaID, err
	} else if err := binary.Write(buf, binary.BigEndian, m.Thumbnail); err != nil {
		return mediaID, err
	}

	return networkid.MediaID(buf.Bytes()), nil
}

func ParseDirectMediaInfo(mediaID networkid.MediaID) (info DirectMediaInfo, err error) {
	if len(mediaID) == 0 {
		return info, fmt.Errorf("empty media ID")
	}

	buf := bytes.NewBuffer(mediaID)

	// version byte
	var version byte
	if err := binary.Read(buf, binary.BigEndian, &version); err != nil {
		return info, err
	} else if version != 0 {
		return info, fmt.Errorf("invalid version %d", version)
	}

	// v0
	var peerType byte
	if err := binary.Read(buf, binary.BigEndian, &peerType); err != nil {
		return info, fmt.Errorf("failed to read peer type: %w", err)
	} else if info.PeerType, err = PeerTypeFromByte(peerType); err != nil {
		return info, fmt.Errorf("failed to convert peer type: %w", err)
	} else if err := binary.Read(buf, binary.BigEndian, &info.PeerID); err != nil {
		return info, fmt.Errorf("failed to read peer id: %w", err)
	} else if err := binary.Read(buf, binary.BigEndian, &info.UserID); err != nil {
		return info, fmt.Errorf("failed to read user id: %w", err)
	} else if err := binary.Read(buf, binary.BigEndian, &info.MessageID); err != nil {
		return info, fmt.Errorf("failed to message id: %w", err)
	} else if err := binary.Read(buf, binary.BigEndian, &info.ID); err != nil {
		return info, fmt.Errorf("failed to media id: %w", err)
	} else if err := binary.Read(buf, binary.BigEndian, &info.Thumbnail); err != nil {
		return info, fmt.Errorf("failed to thumbnail flag: %w", err)
	}

	return info, nil
}

func HashMediaID(mediaID networkid.MediaID) [32]byte {
	return sha256.Sum256(mediaID)
}
