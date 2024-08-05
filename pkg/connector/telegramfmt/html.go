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
)

func (m Mention) Format(message string) string {
	if m.Username != "" {
		return fmt.Sprintf(`<a href="%s">@%s</a>`, m.MXID.URI().MatrixToURL(), m.Username)
	} else {
		return fmt.Sprintf(`<a href="%s">%s</a>`, m.MXID.URI().MatrixToURL(), m.Name)
	}
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
			return fmt.Sprintf("<pre><code class='language-%s'>%s</code></pre>", s.Language, message)
		} else {
			return fmt.Sprintf("<pre><code>%s</code></pre>", message)
		}
	case StyleEmail:
		return fmt.Sprintf(`<a href='mailto:%s'>%s</a>`, message, message)
	case StyleTextURL:
		if strings.HasPrefix(s.URL, "https://matrix.to/#") {
			return s.URL
		}
		return fmt.Sprintf(`<a href='%s'>%s</a>`, s.URL, message)
	case StyleURL:
		if strings.HasPrefix(s.URL, "https://matrix.to/#") {
			return s.URL
		}
		return fmt.Sprintf(`<a href='%s'>%s</a>`, s.URL, message)
	case StyleCustomEmoji:
		if s.Emoji != "" {
			return s.Emoji
		} else {
			return fmt.Sprintf(
				`<img data-mx-emoticon data-mau-animated-emoji src="%s" height="32" width="32" alt="%s" title="%s"/>`,
				s.EmojiURI, message, message,
			)
		}
	case StyleBotCommand:
		return fmt.Sprintf("<font color='#3771bb'>%s</font>", message)
	case StyleHashtag:
		return fmt.Sprintf("<font color='#3771bb'>%s</font>", message)
	case StyleCashtag:
		return fmt.Sprintf("<font color='#3771bb'>%s</font>", message)
	case StylePhone:
		return fmt.Sprintf("<font color='#3771bb'>%s</font>", message)
	default:
		return message
	}
}

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
