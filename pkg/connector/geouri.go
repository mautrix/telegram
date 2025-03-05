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

package connector

import (
	"fmt"
	"strconv"
	"strings"
)

// TODO this should probably be moved to mautrix-go

type GeoURI struct {
	Lat  float64
	Long float64
}

func GeoURIFromLatLong(lat, long float64) GeoURI {
	return GeoURI{lat, long}
}

func ParseGeoURI(uri string) (g GeoURI, err error) {
	if !strings.HasPrefix(uri, "geo:") {
		return g, fmt.Errorf("invalid geo URI: %s", uri)
	}
	coordinates := strings.Split(strings.TrimPrefix(uri, "geo:"), ";")[0]
	parts := strings.Split(coordinates, ",")
	if len(parts) != 2 {
		return g, fmt.Errorf("geo coordinates not formatted properly")
	}
	g.Lat, err = strconv.ParseFloat(parts[0], 64)
	if err != nil {
		return g, fmt.Errorf("failed to parse latitude: %w", err)
	}
	g.Long, err = strconv.ParseFloat(parts[1], 64)
	if err != nil {
		return g, fmt.Errorf("failed to parse longitude: %w", err)
	}
	return
}

func (g GeoURI) URI() string {
	return fmt.Sprintf("geo:%f,%f", g.Lat, g.Long)
}
