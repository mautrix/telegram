// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
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

package tljson

import (
	"fmt"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func Parse(v tg.JSONValueClass) (out any, err error) {
	switch val := v.(type) {
	case *tg.JSONBool:
		return val.Value, nil
	case *tg.JSONNumber:
		return val.Value, nil
	case *tg.JSONString:
		return val.Value, nil
	case *tg.JSONArray:
		out := make([]any, len(val.Value))
		for i, entry := range val.Value {
			out[i], err = Parse(entry)
			if err != nil {
				return nil, err
			}
		}
		return out, nil
	case *tg.JSONObject:
		out := make(map[string]any, len(val.Value))
		for _, entry := range val.Value {
			out[entry.Key], err = Parse(entry.Value)
			if err != nil {
				return nil, err
			}
		}
		return out, nil
	case *tg.JSONNull:
		return nil, nil
	default:
		return nil, fmt.Errorf("unknown JSON value type %T", v)
	}
}
