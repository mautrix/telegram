// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2024 Sumner Evans
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

package telegramfmt

import (
	"context"
	"fmt"
	"html"
	"strings"

	"github.com/gotd/td/tg"
	"golang.org/x/exp/maps"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

type UserInfo struct {
	MXID id.UserID
	Name string
}

type FormatParams struct {
	CustomEmojis map[networkid.EmojiID]string
	GetUserInfo  func(ctx context.Context, id networkid.UserID) (UserInfo, error)
	NormalizeURL func(ctx context.Context, url string) string
}

func (fp FormatParams) GetCustomEmoji(emojiID networkid.EmojiID) (string, id.ContentURIString) {
	if strings.HasPrefix(fp.CustomEmojis[emojiID], "mxc://") {
		return "", id.ContentURIString(fp.CustomEmojis[emojiID])
	} else {
		return fp.CustomEmojis[emojiID], ""
	}
}

func (fp FormatParams) WithCustomEmojis(emojis map[networkid.EmojiID]string) FormatParams {
	return FormatParams{
		CustomEmojis: emojis,
		GetUserInfo:  fp.GetUserInfo,
		NormalizeURL: fp.NormalizeURL,
	}
}

type formatContext struct {
	IsInCodeblock bool
}

func (ctx formatContext) TextToHTML(text string) string {
	if ctx.IsInCodeblock {
		return html.EscapeString(text)
	}
	return event.TextToHTML(text)
}

func Parse(ctx context.Context, message string, entities []tg.MessageEntityClass, params FormatParams) (*event.MessageEventContent, error) {
	content := &event.MessageEventContent{
		MsgType:  event.MsgText,
		Body:     message,
		Mentions: &event.Mentions{},
	}
	if len(entities) == 0 {
		return content, nil
	}

	lrt := &LinkedRangeTree{}
	mentions := map[id.UserID]struct{}{}
	utf16Message := NewUTF16String(message)
	maxLength := len(utf16Message)
	for _, e := range entities {
		br := BodyRange{
			Start:  e.GetOffset(),
			Length: e.GetLength(),
		}.TruncateEnd(maxLength)
		switch entity := e.(type) {
		case *tg.MessageEntityMention:
			// TODO
			fmt.Printf("mention = %+v\n", entity)
		case *tg.MessageEntityHashtag:
			br.Value = Style{Type: StyleHashtag}
		case *tg.MessageEntityBotCommand:
			br.Value = Style{Type: StyleBotCommand}
		case *tg.MessageEntityURL:
			br.Value = Style{Type: StyleURL, URL: params.NormalizeURL(ctx, utf16Message[e.GetOffset():e.GetOffset()+e.GetLength()].String())}
		case *tg.MessageEntityEmail:
			br.Value = Style{Type: StyleEmail}
		case *tg.MessageEntityBold:
			br.Value = Style{Type: StyleBold}
		case *tg.MessageEntityItalic:
			br.Value = Style{Type: StyleItalic}
		case *tg.MessageEntityCode:
			br.Value = Style{Type: StyleCode}
		case *tg.MessageEntityPre:
			br.Value = Style{Type: StylePre, Language: entity.Language}
		case *tg.MessageEntityTextURL:
			br.Value = Style{Type: StyleURL, URL: params.NormalizeURL(ctx, entity.URL)}
		case *tg.MessageEntityMentionName:
			userID := ids.MakeUserID(entity.UserID)
			userInfo, err := params.GetUserInfo(ctx, userID)
			if err != nil {
				return nil, err
			}
			mentions[userInfo.MXID] = struct{}{}
			br.Value = Mention{UserInfo: userInfo, UserID: userID}
		case *tg.MessageEntityPhone:
			br.Value = Style{Type: StylePhone}
		case *tg.MessageEntityCashtag:
			br.Value = Style{Type: StyleCashtag}
		case *tg.MessageEntityUnderline:
			br.Value = Style{Type: StyleUnderline}
		case *tg.MessageEntityStrike:
			br.Value = Style{Type: StyleStrikethrough}
		case *tg.MessageEntityBankCard:
			br.Value = Style{Type: StyleBankCard}
		case *tg.MessageEntitySpoiler:
			br.Value = Style{Type: StyleSpoiler}
		case *tg.MessageEntityCustomEmoji:
			emoji, contentURI := params.GetCustomEmoji(ids.MakeEmojiIDFromDocumentID(entity.DocumentID))
			if emoji != "" {
				br.Value = Style{Type: StyleCustomEmoji, Emoji: emoji}
			} else {
				br.Value = Style{Type: StyleCustomEmoji, EmojiURI: contentURI}
			}
		case *tg.MessageEntityBlockquote:
			br.Value = Style{Type: StyleBlockquote}
		}
		lrt.Add(br)
	}

	content.Mentions.UserIDs = maps.Keys(mentions)
	content.FormattedBody = lrt.Format(utf16Message, formatContext{})
	content.Format = event.FormatHTML
	return content, nil
}
