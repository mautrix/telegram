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

package matrixfmt

import (
	"context"
	"fmt"

	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/telegramfmt"
)

func toTelegramEntity(br telegramfmt.BodyRange) tg.MessageEntityClass {
	switch val := br.Value.(type) {
	case telegramfmt.Mention:
		if val.Username != "" {
			return &tg.MessageEntityMention{Offset: br.Start, Length: br.Length}
		} else {
			peerType, userID, _ := ids.ParseUserID(val.UserID)
			if peerType != ids.PeerTypeUser {
				panic(fmt.Errorf("unexpected peer type in mention %T", peerType))
			}
			return &tg.InputMessageEntityMentionName{
				Offset: br.Start,
				Length: br.Length,
				UserID: &tg.InputUser{UserID: userID, AccessHash: val.AccessHash},
			}
		}
	case telegramfmt.Style:
		switch val.Type {
		case telegramfmt.StyleBold:
			return &tg.MessageEntityBold{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleItalic:
			return &tg.MessageEntityItalic{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleUnderline:
			return &tg.MessageEntityUnderline{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleStrikethrough:
			return &tg.MessageEntityStrike{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleBlockquote:
			return &tg.MessageEntityBlockquote{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleCode:
			return &tg.MessageEntityCode{Offset: br.Start, Length: br.Length}
		case telegramfmt.StylePre:
			return &tg.MessageEntityPre{Offset: br.Start, Length: br.Length, Language: val.Language}
		case telegramfmt.StyleEmail:
			return &tg.MessageEntityEmail{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleTextURL:
			return &tg.MessageEntityTextURL{Offset: br.Start, Length: br.Length, URL: val.URL}
		case telegramfmt.StyleURL:
			return &tg.MessageEntityURL{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleBotCommand:
			return &tg.MessageEntityBotCommand{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleHashtag:
			return &tg.MessageEntityHashtag{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleCashtag:
			return &tg.MessageEntityCashtag{Offset: br.Start, Length: br.Length}
		case telegramfmt.StylePhone:
			return &tg.MessageEntityPhone{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleSpoiler:
			return &tg.MessageEntitySpoiler{Offset: br.Start, Length: br.Length}
		case telegramfmt.StyleBankCard:
			return &tg.MessageEntityBankCard{Offset: br.Start, Length: br.Length}
		default:
			panic("unsupported style type")
		}
	default:
		panic("unknown body range value")
	}
}

func Parse(ctx context.Context, parser *HTMLParser, content *event.MessageEventContent) (string, []tg.MessageEntityClass) {
	if content.MsgType.IsMedia() && (content.FileName == "" || content.FileName == content.Body) {
		// The body is the filename.
		return "", nil
	}

	if content.Format != event.FormatHTML {
		return content.Body, nil
	}
	parseCtx := NewContext(ctx)
	parseCtx.AllowedMentions = content.Mentions
	parsed := parser.Parse(content.FormattedBody, parseCtx)
	if parsed == nil {
		return "", nil
	}
	var entities []tg.MessageEntityClass
	if len(parsed.Entities) > 0 {
		entities = make([]tg.MessageEntityClass, len(parsed.Entities))
		for i, ent := range parsed.Entities {
			entities[i] = toTelegramEntity(ent)
		}
	}
	return parsed.String.String(), entities
}
