// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 David Jirovec
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
	"testing"

	"github.com/stretchr/testify/assert"
	"maunium.net/go/mautrix/event"
)

func TestGetUserLocalTag(t *testing.T) {
	tests := []struct {
		name     string
		pinned   bool
		folderID int
		expected event.RoomTag
	}{
		{
			name:     "regular dialog",
			expected: "",
		},
		{
			name:     "pinned dialog",
			pinned:   true,
			expected: event.RoomTagFavourite,
		},
		{
			name:     "archived dialog",
			folderID: telegramArchiveFolderID,
			expected: event.RoomTagLowPriority,
		},
		{
			name:     "pinned archived dialog",
			pinned:   true,
			folderID: telegramArchiveFolderID,
			expected: event.RoomTagFavourite,
		},
		{
			name:     "unsupported folder",
			folderID: 2,
			expected: "",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			assert.Equal(t, test.expected, getUserLocalTag(test.pinned, test.folderID))
		})
	}
}
