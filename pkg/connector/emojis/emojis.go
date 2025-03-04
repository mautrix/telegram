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
