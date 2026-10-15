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
	"encoding/json"
	"fmt"
	"html"
	"strconv"
	"strings"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

type BotButtonType string

const (
	// BotButtonCallback is an inline button that sends callback data to the bot.
	BotButtonCallback BotButtonType = "callback"
	// BotButtonGame is an inline button that launches a game.
	BotButtonGame BotButtonType = "game"
	// BotButtonURL is an inline button that opens a link.
	BotButtonURL BotButtonType = "url"
	// BotButtonText is a reply keyboard button that sends its own text as a message.
	BotButtonText BotButtonType = "text"
	// BotButtonCopy is an inline button that copies some text to the clipboard.
	BotButtonCopy BotButtonType = "copy"
	// BotButtonUnsupported is any other button, which is only displayed.
	BotButtonUnsupported BotButtonType = "unsupported"
)

type BotButton struct {
	Type     BotButtonType `json:"type"`
	Text     string        `json:"text"`
	Data     []byte        `json:"data,omitempty"`
	URL      string        `json:"url,omitempty"`
	CopyText string        `json:"copy_text,omitempty"`
}

// BotButtons is the keyboard attached to a bot message. Buttons are numbered
// sequentially across rows starting from 1.
type BotButtons [][]BotButton

func (bb BotButtons) Get(number int) *BotButton {
	if number < 1 {
		return nil
	}
	for _, row := range bb {
		if number <= len(row) {
			return &row[number-1]
		}
		number -= len(row)
	}
	return nil
}

func convertKeyboardButton(button tg.KeyboardButtonClass) BotButton {
	converted := BotButton{Type: BotButtonUnsupported, Text: button.GetText()}
	switch button := button.(type) {
	case *tg.KeyboardButton:
		converted.Type = BotButtonText
	case *tg.KeyboardButtonCallback:
		// Callbacks protected by 2FA would require the user's cloud password.
		if !button.RequiresPassword {
			converted.Type = BotButtonCallback
			converted.Data = button.Data
		}
	case *tg.KeyboardButtonGame:
		converted.Type = BotButtonGame
	case *tg.KeyboardButtonURL:
		converted.Type = BotButtonURL
		converted.URL = button.URL
	case *tg.KeyboardButtonURLAuth:
		converted.Type = BotButtonURL
		converted.URL = button.URL
	case *tg.KeyboardButtonWebView:
		converted.Type = BotButtonURL
		converted.URL = button.URL
	case *tg.KeyboardButtonSimpleWebView:
		converted.Type = BotButtonURL
		converted.URL = button.URL
	case *tg.KeyboardButtonCopy:
		converted.Type = BotButtonCopy
		converted.CopyText = button.CopyText
	}
	return converted
}

func convertReplyMarkup(markup tg.ReplyMarkupClass) BotButtons {
	var rows []tg.KeyboardButtonRow
	switch markup := markup.(type) {
	case *tg.ReplyInlineMarkup:
		rows = markup.Rows
	case *tg.ReplyKeyboardMarkup:
		rows = markup.Rows
	default:
		return nil
	}
	var buttons BotButtons
	for _, row := range rows {
		convertedRow := make([]BotButton, 0, len(row.Buttons))
		for _, button := range row.Buttons {
			convertedRow = append(convertedRow, convertKeyboardButton(button))
		}
		if len(convertedRow) > 0 {
			buttons = append(buttons, convertedRow)
		}
	}
	return buttons
}

func (bb BotButtons) hashData() []byte {
	data, _ := json.Marshal(bb)
	return data
}

// addToContent renders the buttons as numbered lines below the message body,
// one line per keyboard row.
func (bb BotButtons) addToContent(content *event.MessageEventContent) {
	if content.MsgType.IsMedia() && content.FileName == "" {
		// Keep the original body as the file name so that the buttons become a caption
		content.FileName = content.Body
		content.Body = ""
	}
	content.EnsureHasHTML()
	var plain, formatted strings.Builder
	number := 1
	for _, row := range bb {
		for i, button := range row {
			if i > 0 {
				plain.WriteString("   ")
				formatted.WriteString(" &nbsp; ")
			}
			plain.WriteString(fmt.Sprintf("[%d] %s", number, button.Text))
			formatted.WriteString(fmt.Sprintf("<b>[%d]</b> ", number))
			if button.Type == BotButtonURL {
				formatted.WriteString(fmt.Sprintf(`<a href="%s">%s</a>`, html.EscapeString(button.URL), html.EscapeString(button.Text)))
			} else {
				formatted.WriteString(html.EscapeString(button.Text))
			}
			number++
		}
		plain.WriteString("\n")
		formatted.WriteString("<br>")
	}
	if content.Body != "" {
		content.Body += "\n\n"
	}
	content.Body += strings.TrimSuffix(plain.String(), "\n")
	content.FormattedBody += "<p data-telegram-bot-buttons>" + strings.TrimSuffix(formatted.String(), "<br>") + "</p>"
}

// recentKeyboardLookback is how many recent messages are checked for buttons
// when a button number is sent without a reply.
const recentKeyboardLookback = 20

// parseBotButtonNumber parses a button number like "2" or "[2]".
func parseBotButtonNumber(body string) (int, bool) {
	body = strings.TrimSpace(body)
	if strings.HasPrefix(body, "[") && strings.HasSuffix(body, "]") {
		body = body[1 : len(body)-1]
	}
	number, err := strconv.Atoi(body)
	return number, err == nil
}

func (bb BotButtons) hasKeyboardButtons() bool {
	for _, row := range bb {
		for _, button := range row {
			if button.Type == BotButtonText {
				return true
			}
		}
	}
	return false
}

// findBotButtonPress checks whether the given Matrix message is a button press.
// A reply to a message with buttons that consists of just a button number (like
// "2" or "[2]") presses that button. If plain_numbers is enabled, a button number
// without a reply in a private chat presses a button on the latest message from
// the bot, or on the most recent reply keyboard, since reply keyboards are
// attached to the chat rather than a specific message on Telegram. Numbers that
// don't match any button are sent as normal text.
func (tc *TelegramClient) findBotButtonPress(ctx context.Context, msg *bridgev2.MatrixMessage) (*BotButton, *database.Message) {
	if msg.Content.MsgType != event.MsgText {
		return nil, nil
	}
	number, ok := parseBotButtonNumber(msg.Content.Body)
	if !ok {
		return nil, nil
	}
	if msg.ReplyTo != nil {
		meta, ok := msg.ReplyTo.Metadata.(*MessageMetadata)
		if !ok {
			return nil, nil
		}
		return meta.BotButtons.Get(number), msg.ReplyTo
	}
	if !tc.main.Config.BotButtons.PlainNumbers {
		return nil, nil
	} else if peerType, _, _, err := ids.ParsePortalID(msg.Portal.ID); err != nil || peerType != ids.PeerTypeUser {
		return nil, nil
	}
	recent, err := tc.main.Bridge.DB.Message.GetLastNInPortal(ctx, msg.Portal.PortalKey, recentKeyboardLookback)
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Msg("Failed to get recent messages to find bot buttons")
		return nil, nil
	}
	isLatest := true
	for _, dbMsg := range recent {
		meta, ok := dbMsg.Metadata.(*MessageMetadata)
		if !ok || dbMsg.SenderID == tc.userID {
			continue
		}
		// Inline buttons can only be pressed on the latest message from the bot,
		// while reply keyboards stay active until the bot replaces them.
		if (isLatest && len(meta.BotButtons) > 0) || meta.BotButtons.hasKeyboardButtons() {
			return meta.BotButtons.Get(number), dbMsg
		}
		isLatest = false
	}
	return nil, nil
}

// pressBotButton handles pressing a button that doesn't result in a message
// being sent to the chat. Text buttons are handled by the normal send path.
func (tc *TelegramClient) pressBotButton(ctx context.Context, msg *bridgev2.MatrixMessage, peer tg.InputPeerClass, button *BotButton, target *database.Message) (*bridgev2.MatrixMessageResponse, error) {
	log := zerolog.Ctx(ctx).With().
		Str("action", "press_bot_button").
		Str("button_type", string(button.Type)).
		Logger()
	var notice, noticeHTML string
	switch button.Type {
	case BotButtonCallback, BotButtonGame:
		_, messageID, err := ids.ParseMessageID(target.ID)
		if err != nil {
			return nil, fmt.Errorf("failed to parse message ID: %w", err)
		}
		req := &tg.MessagesGetBotCallbackAnswerRequest{
			Peer:  peer,
			MsgID: messageID,
		}
		if button.Type == BotButtonGame {
			req.Game = true
		} else {
			req.SetData(button.Data)
		}
		answer, err := tc.client.API().MessagesGetBotCallbackAnswer(ctx, req)
		if tg.IsBotResponseTimeout(err) {
			// The button press was delivered, the bot just didn't send any answer in time.
			log.Debug().Msg("Bot didn't answer callback query in time")
		} else if err != nil {
			log.Err(err).Msg("Failed to press bot button")
			return nil, tc.humaniseSendError(err)
		} else {
			notice = answer.Message
			noticeHTML = html.EscapeString(answer.Message)
			if answer.HasURL {
				notice = strings.TrimSpace(notice + "\n" + answer.URL)
				noticeHTML = strings.TrimPrefix(noticeHTML+"<br>", "<br>") + fmt.Sprintf(`<a href="%s">%s</a>`, html.EscapeString(answer.URL), html.EscapeString(answer.URL))
			}
		}
	case BotButtonURL:
		notice = fmt.Sprintf("%s: %s", button.Text, button.URL)
		noticeHTML = fmt.Sprintf(`<a href="%s">%s</a>`, html.EscapeString(button.URL), html.EscapeString(button.Text))
	case BotButtonCopy:
		notice = button.CopyText
		noticeHTML = fmt.Sprintf("<code>%s</code>", html.EscapeString(button.CopyText))
	default:
		return nil, bridgev2.WrapErrorInStatus(fmt.Errorf("pressing this type of button is not supported")).
			WithErrorReason(event.MessageStatusUnsupported).
			WithIsCertain(true).
			WithErrorAsMessage().
			WithSendNotice(true)
	}
	if notice != "" {
		tc.sendBotButtonNotice(ctx, msg, target, notice, noticeHTML)
	}
	log.Debug().Msg("Pressed bot button")
	// Nothing was sent to the chat, so there's no message to save.
	tc.main.Bridge.Matrix.SendMessageStatus(ctx, &bridgev2.MessageStatus{
		Status:    event.MessageStatusSuccess,
		IsCertain: true,
	}, bridgev2.StatusEventInfoFromEvent(msg.Event))
	return &bridgev2.MatrixMessageResponse{Pending: true}, nil
}

// sendBotButtonNotice sends a local notice as the bot, which is used for
// things like callback query answers that don't exist as messages on Telegram.
func (tc *TelegramClient) sendBotButtonNotice(ctx context.Context, msg *bridgev2.MatrixMessage, target *database.Message, text, htmlText string) {
	log := zerolog.Ctx(ctx)
	ghost, err := tc.main.Bridge.GetGhostByID(ctx, target.SenderID)
	if err != nil || ghost == nil {
		log.Err(err).Msg("Failed to get bot ghost to send button answer")
		return
	}
	content := &event.MessageEventContent{
		MsgType:       event.MsgNotice,
		Body:          text,
		Format:        event.FormatHTML,
		FormattedBody: htmlText,
		RelatesTo:     (&event.RelatesTo{}).SetReplyTo(msg.Event.ID),
	}
	_, err = ghost.Intent.SendMessage(ctx, msg.Portal.MXID, event.EventMessage, &event.Content{
		Parsed: content,
		Raw:    map[string]any{"fi.mau.telegram.bot_callback_answer": true},
	}, nil)
	if err != nil {
		log.Err(err).Msg("Failed to send bot button answer notice")
	}
}
