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

	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/id"
)

type BodyRangeValue interface {
	String() string
	Format(message string) string
	IsCode() bool
}

type Mention struct {
	UserInfo
	UserID networkid.UserID
}

var _ BodyRangeValue = Mention{}

func (m Mention) String() string {
	return fmt.Sprintf("Mention{MXID: id.UserID(%q), Name: %q}", m.MXID, m.Name)
}

func (m Mention) IsCode() bool {
	return false
}

type StyleType int

var _ BodyRangeValue = Mention{}

const (
	StyleNone StyleType = iota
	StyleBold
	StyleItalic
	StyleUnderline
	StyleStrikethrough
	StyleBlockquote
	StyleCode
	StylePre
	StyleEmail
	StyleTextURL
	StyleURL
	StyleCustomEmoji
	StyleBotCommand
	StyleHashtag
	StyleCashtag
	StylePhone
	StyleSpoiler
	StyleBankCard
)

func (s StyleType) String() string {
	switch s {
	case StyleNone:
		return "StyleNone"
	case StyleBold:
		return "StyleBold"
	case StyleItalic:
		return "StyleItalic"
	case StyleUnderline:
		return "StyleUnderline"
	case StyleStrikethrough:
		return "StyleStrikethrough"
	case StyleBlockquote:
		return "StyleBlockquote"
	case StyleCode:
		return "StyleCode"
	case StylePre:
		return "StylePre"
	case StyleEmail:
		return "StyleEmail"
	case StyleTextURL:
		return "StyleTextURL"
	case StyleURL:
		return "StyleEntityURL"
	case StyleCustomEmoji:
		return "StyleCustomEmoji"
	case StyleBotCommand:
		return "StyleBotCommand"
	case StyleHashtag:
		return "StyleHashtag"
	case StyleCashtag:
		return "StyleCashtag"
	case StylePhone:
		return "StylePhone"
	case StyleSpoiler:
		return "StyleSpoiler"
	case StyleBankCard:
		return "StyleBankCard"
	default:
		return fmt.Sprintf("StyleType(%d)", s)
	}
}

// Style represents a style to apply to a range of text.
type Style struct {
	// Type is the type of style.
	Type StyleType

	// Language is the language of the code block, if applicable.
	Language string

	// URL is the URL to link to, if applicable.
	URL string

	// Emoji is the emoji to display, if applicable.
	Emoji string

	// EmojiURI is the URI to the emoji, if applicable.
	EmojiURI id.ContentURIString
}

func (s Style) String() string {
	return fmt.Sprintf("Style{Type: %s, Language: %s, URL: %s}", s.Type, s.Language, s.URL)
}

func (s Style) IsCode() bool {
	return s.Type == StyleCode || s.Type == StylePre
}
