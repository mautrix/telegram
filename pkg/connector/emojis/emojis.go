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

package emojis

import (
	_ "embed"
	"encoding/json"

	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

//go:embed unicodemojipack.json
var unicodemojiPackJSON []byte

var unicodemojiPack = map[string]int64{}
var reverseUnicodemojiPack = map[int64]string{}

func init() {
	if err := json.Unmarshal(unicodemojiPackJSON, &unicodemojiPack); err != nil {
		panic("Failed to unmarshal unicodemojipack")
	}

	for k, v := range unicodemojiPack {
		reverseUnicodemojiPack[v] = k
	}
}

// ConvertKnownEmojis converts known document IDs from the unicode emoji pack
// to the corresponding unicode string and returns the remaining IDs.
func ConvertKnownEmojis(emojiIDs []int64) (result map[networkid.EmojiID]EmojiInfo, remaining []int64) {
	result = map[networkid.EmojiID]EmojiInfo{}
	for _, e := range emojiIDs {
		if v, ok := reverseUnicodemojiPack[e]; ok {
			result[ids.MakeEmojiIDFromDocumentID(e)] = EmojiInfo{Emoji: v}
		} else {
			remaining = append(remaining, e)
		}
	}
	return
}

func GetEmojiDocumentID(emoji string) (int64, bool) {
	id, ok := unicodemojiPack[emoji]
	return id, ok
}

// EmojiInfo contains information about an emoji.
type EmojiInfo struct {
	Emoji    string
	EmojiURI id.ContentURIString
}
