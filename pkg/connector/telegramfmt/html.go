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
	"fmt"
	"strings"
	"unicode/utf16"

	"go.mau.fi/mautrix-telegram/pkg/connector/emojis"
)

func (m Mention) Format(message string) string {
	if m.Username != "" {
		return fmt.Sprintf(`<a href="%s">@%s</a>`, m.MXID.URI().MatrixToURL(), m.Username)
	}
	return fmt.Sprintf(`<a href="%s">%s</a>`, m.MXID.URI().MatrixToURL(), m.Name)
}

func (s Style) Format(message string) string {
	switch s.Type {
	case StyleBold:
		return fmt.Sprintf("<strong>%s</strong>", message)
	case StyleItalic:
		return fmt.Sprintf("<em>%s</em>", message)
	case StyleSpoiler:
		return fmt.Sprintf("<span data-mx-spoiler>%s</span>", message)
	case StyleStrikethrough:
		return fmt.Sprintf("<del>%s</del>", message)
	case StyleCode:
		if strings.ContainsRune(message, '\n') {
			// This is somewhat incorrect, as it won't allow inline text before/after a multiline monospace-formatted string.
			return fmt.Sprintf("<pre><code>%s</code></pre>", message)
		}
		return fmt.Sprintf("<code>%s</code>", message)
	case StyleUnderline:
		return fmt.Sprintf("<u>%s</u>", message)
	case StyleBlockquote:
		return fmt.Sprintf("<blockquote>%s</blockquote>", message)
	case StylePre:
		if s.Language != "" {
			return fmt.Sprintf(`<pre><code class="language-%s">%s</code></pre>`, s.Language, message)
		}
		return fmt.Sprintf("<pre><code>%s</code></pre>", message)
	case StyleEmail:
		return fmt.Sprintf(`<a href="mailto:%s">%s</a>`, message, message)
	case StyleTextURL, StyleURL:
		return fmt.Sprintf(`<a href="%s">%s</a>`, s.URL, message)
	case StyleCustomEmoji:
		return emojiInfoToHTML(s.EmojiInfo, message)
	case StyleBotCommand, StyleHashtag, StyleCashtag, StylePhone, StyleBankCard:
		return fmt.Sprintf(`<span data-mx-color="%s">%s</font>`, hashColor, message)
	default:
		return message
	}
}

func emojiInfoToHTML(info emojis.EmojiInfo, fallback string) string {
	if info.Emoji != "" {
		return info.Emoji
	} else if info.EmojiURI != "" {
		return fmt.Sprintf(
			`<img data-mx-emoticon src="%s" height="32" width="32" alt="%s" title="%s"/>`,
			info.EmojiURI, fallback, fallback,
		)
	}
	return fallback
}

const hashColor = "#3771bb"
const highlightBackgroundColor = "#fff4a3"

type UTF16String []uint16

func NewUTF16String(s string) UTF16String {
	return utf16.Encode([]rune(s))
}

func (u UTF16String) String() string {
	return string(utf16.Decode(u))
}

func (lrt *LinkedRangeTree) Format(message UTF16String, ctx formatContext) string {
	if lrt == nil || lrt.Node == nil {
		return ctx.TextToHTML(message.String())
	}
	head := message[:lrt.Node.Start]
	headStr := ctx.TextToHTML(head.String())
	inner := message[lrt.Node.Start:lrt.Node.End()]
	tail := message[lrt.Node.End():]
	ourCtx := ctx
	if lrt.Node.Value.IsCode() {
		ourCtx.IsInCodeblock = true
	}
	childMessage := lrt.Child.Format(inner, ourCtx)
	formattedChildMessage := lrt.Node.Value.Format(childMessage)
	siblingMessage := lrt.Sibling.Format(tail, ctx)
	return headStr + formattedChildMessage + siblingMessage
}
