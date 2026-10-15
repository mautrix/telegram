// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 highda
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

package connector

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestConvertReplyMarkup_Inline(t *testing.T) {
	buttons := convertReplyMarkup(&tg.ReplyInlineMarkup{Rows: []tg.KeyboardButtonRow{
		{Buttons: []tg.KeyboardButtonClass{
			&tg.KeyboardButtonCallback{Text: "Yes", Data: []byte("y")},
			&tg.KeyboardButtonCallback{Text: "No", Data: []byte("n")},
		}},
		{Buttons: []tg.KeyboardButtonClass{
			&tg.KeyboardButtonURL{Text: "Docs", URL: "https://example.com"},
			&tg.KeyboardButtonCallback{Text: "Secret", Data: []byte("s"), RequiresPassword: true},
			&tg.KeyboardButtonCopy{Text: "Copy", CopyText: "abc"},
			&tg.KeyboardButtonSwitchInline{Text: "Share"},
		}},
	}})
	require.Len(t, buttons, 2)
	assert.Equal(t, []BotButton{
		{Type: BotButtonCallback, Text: "Yes", Data: []byte("y")},
		{Type: BotButtonCallback, Text: "No", Data: []byte("n")},
	}, buttons[0])
	assert.Equal(t, []BotButton{
		{Type: BotButtonURL, Text: "Docs", URL: "https://example.com"},
		{Type: BotButtonUnsupported, Text: "Secret"},
		{Type: BotButtonCopy, Text: "Copy", CopyText: "abc"},
		{Type: BotButtonUnsupported, Text: "Share"},
	}, buttons[1])
}

func TestConvertReplyMarkup_Keyboard(t *testing.T) {
	buttons := convertReplyMarkup(&tg.ReplyKeyboardMarkup{Rows: []tg.KeyboardButtonRow{
		{Buttons: []tg.KeyboardButtonClass{
			&tg.KeyboardButton{Text: "Menu"},
			&tg.KeyboardButtonRequestPhone{Text: "Share phone"},
		}},
	}})
	assert.Equal(t, BotButtons{{
		{Type: BotButtonText, Text: "Menu"},
		{Type: BotButtonUnsupported, Text: "Share phone"},
	}}, buttons)
}

func TestConvertReplyMarkup_NoButtons(t *testing.T) {
	assert.Nil(t, convertReplyMarkup(nil))
	assert.Nil(t, convertReplyMarkup(&tg.ReplyKeyboardHide{}))
	assert.Nil(t, convertReplyMarkup(&tg.ReplyInlineMarkup{}))
}

func TestBotButtons_Get(t *testing.T) {
	buttons := BotButtons{
		{{Text: "a"}, {Text: "b"}},
		{{Text: "c"}},
	}
	assert.Nil(t, buttons.Get(0))
	assert.Equal(t, "a", buttons.Get(1).Text)
	assert.Equal(t, "b", buttons.Get(2).Text)
	assert.Equal(t, "c", buttons.Get(3).Text)
	assert.Nil(t, buttons.Get(4))
}

func TestBotButtons_AddToContent(t *testing.T) {
	content := &event.MessageEventContent{MsgType: event.MsgText, Body: "Pick one"}
	BotButtons{
		{{Type: BotButtonCallback, Text: "Yes"}, {Type: BotButtonCallback, Text: "No"}},
		{{Type: BotButtonURL, Text: "Docs <3", URL: "https://example.com/?a=1&b=2"}},
	}.addToContent(content)
	assert.Equal(t, "Pick one\n\n[1] Yes   [2] No\n[3] Docs <3", content.Body)
	assert.Equal(t, event.FormatHTML, content.Format)
	assert.Equal(t, `Pick one<p data-telegram-bot-buttons><b>[1]</b> Yes &nbsp; <b>[2]</b> No<br>`+
		`<b>[3]</b> <a href="https://example.com/?a=1&amp;b=2">Docs &lt;3</a></p>`, content.FormattedBody)
}

func TestBotButtons_AddToContent_MediaWithoutCaption(t *testing.T) {
	content := &event.MessageEventContent{MsgType: event.MsgImage, Body: "photo.jpg"}
	BotButtons{{{Type: BotButtonCallback, Text: "Next"}}}.addToContent(content)
	assert.Equal(t, "photo.jpg", content.FileName)
	assert.Equal(t, "[1] Next", content.Body)
}

func TestParseBotButtonNumber(t *testing.T) {
	for body, expected := range map[string]int{"2": 2, " 2 ": 2, "[2]": 2, " [10] ": 10} {
		number, ok := parseBotButtonNumber(body)
		assert.True(t, ok, body)
		assert.Equal(t, expected, number, body)
	}
	for _, body := range []string{"", "2 please", "[2", "two", "[]"} {
		_, ok := parseBotButtonNumber(body)
		assert.False(t, ok, body)
	}
}

func TestFindBotButtonPress_Reply(t *testing.T) {
	tc := &TelegramClient{main: &TelegramConnector{}}
	replyTo := &database.Message{Metadata: &MessageMetadata{BotButtons: BotButtons{
		{{Type: BotButtonCallback, Text: "Yes"}, {Type: BotButtonCallback, Text: "No"}},
	}}}
	makeMsg := func(body string, replyTo *database.Message) *bridgev2.MatrixMessage {
		return &bridgev2.MatrixMessage{
			MatrixEventBase: bridgev2.MatrixEventBase[*event.MessageEventContent]{
				Content: &event.MessageEventContent{MsgType: event.MsgText, Body: body},
				Portal:  &bridgev2.Portal{Portal: &database.Portal{PortalKey: networkid.PortalKey{ID: "chat:123"}}},
			},
			ReplyTo: replyTo,
		}
	}
	button, target := tc.findBotButtonPress(context.Background(), makeMsg(" [2] ", replyTo))
	require.NotNil(t, button)
	assert.Equal(t, "No", button.Text)
	assert.Equal(t, replyTo, target)
	button, _ = tc.findBotButtonPress(context.Background(), makeMsg("3", replyTo))
	assert.Nil(t, button)
	button, _ = tc.findBotButtonPress(context.Background(), makeMsg("2 please", replyTo))
	assert.Nil(t, button)
	button, _ = tc.findBotButtonPress(context.Background(), makeMsg("1", &database.Message{Metadata: &MessageMetadata{}}))
	assert.Nil(t, button)
	// Without a reply, numbers are sent as text unless plain_numbers is enabled
	button, _ = tc.findBotButtonPress(context.Background(), makeMsg("1", nil))
	assert.Nil(t, button)
	button, _ = tc.findBotButtonPress(context.Background(), makeMsg("[1]", nil))
	assert.Nil(t, button)
	// and even then only in private chats
	tc.main.Config.BotButtons.PlainNumbers = true
	button, _ = tc.findBotButtonPress(context.Background(), makeMsg("1", nil))
	assert.Nil(t, button)
}

func TestBotButtons_HasKeyboardButtons(t *testing.T) {
	assert.False(t, BotButtons{{{Type: BotButtonCallback}}}.hasKeyboardButtons())
	assert.True(t, BotButtons{{{Type: BotButtonUnsupported}}, {{Type: BotButtonText}}}.hasKeyboardButtons())
}
