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

package telegramfmt_test

import (
	"context"
	"fmt"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"

	"go.mau.fi/mautrix-telegram/pkg/connector/telegramfmt"
)

func TestParse(t *testing.T) {
	formatParams := telegramfmt.FormatParams{
		GetUserInfoByID: func(ctx context.Context, userID int64) (telegramfmt.UserInfo, error) {
			if userID == 1 {
				return telegramfmt.UserInfo{
					MXID: "@test:example.com",
					Name: "Matrix User",
				}, nil
			} else {
				return telegramfmt.UserInfo{
					MXID: id.UserID(fmt.Sprintf("@telegram_%d:example.com", userID)),
					Name: "Signal User",
				}, nil
			}
		},
	}
	tests := []struct {
		name string
		ins  string
		ine  []tg.MessageEntityClass
		body string
		html string

		extraChecks func(*testing.T, *event.MessageEventContent)
	}{
		{
			name: "empty",
			extraChecks: func(t *testing.T, content *event.MessageEventContent) {
				assert.Empty(t, content.FormattedBody)
				assert.Empty(t, content.Body)
			},
		},
		{
			name: "plain",
			ins:  "Hello world!",
			body: "Hello world!",
			extraChecks: func(t *testing.T, content *event.MessageEventContent) {
				assert.Empty(t, content.FormattedBody)
				assert.Empty(t, content.Format)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			parsed, err := telegramfmt.Parse(context.TODO(), test.ins, test.ine, formatParams)
			require.NoError(t, err)
			assert.Equal(t, test.body, parsed.Body)
			assert.Equal(t, test.html, parsed.FormattedBody)
		})
	}
}
