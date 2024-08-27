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

package main

import (
	"encoding/base64"
	"fmt"

	"go.mau.fi/util/dbutil/litestream"
	"maunium.net/go/mautrix/bridgev2/bridgeconfig"
	"maunium.net/go/mautrix/bridgev2/matrix/mxmain"

	"go.mau.fi/mautrix-telegram/pkg/connector"
	"go.mau.fi/mautrix-telegram/pkg/connector/store/upgrades"
)

// Information to find out exactly which commit the bridge was built from.
// These are filled at build time with the -X linker flag.
var (
	Tag       = "unknown"
	Commit    = "unknown"
	BuildTime = "unknown"
)

var c = connector.NewConnector()
var m = mxmain.BridgeMain{
	Name:        "mautrix-telegram",
	URL:         "https://github.com/mautrix/telegram",
	Description: "A Matrix-Telegram puppeting bridge.",
	Version:     "0.16.0",

	Connector: c,
}

func init() {
	litestream.Functions["encode"] = func(data []byte, encoding string) string {
		if encoding == "base64" {
			return base64.StdEncoding.EncodeToString(data)
		}
		panic(fmt.Errorf("unknown encoding %q", encoding))
	}
}

func main() {
	bridgeconfig.HackyMigrateLegacyNetworkConfig = migrateLegacyConfig
	versionWithoutCommit := m.Version
	m.PostInit = func() {
		if c.Config.DeviceInfo.AppVersion == "auto" {
			c.Config.DeviceInfo.AppVersion = versionWithoutCommit
		}
		if c.Config.DeviceInfo.SystemVersion == "auto" {
			c.Config.DeviceInfo.SystemVersion = ""
		}
		if c.Config.DeviceInfo.DeviceModel == "auto" || c.Config.DeviceInfo.DeviceModel == "" {
			c.Config.DeviceInfo.DeviceModel = "mautrix-telegram"
		}
		m.CheckLegacyDB(
			18,
			"v0.14.0",
			"v0.16.0",
			m.LegacyMigrateWithAnotherUpgrader(
				legacyMigrateRenameTables, legacyMigrateCopyData, 16,
				upgrades.Table, "telegram_version", 1,
			),
			true,
		)
	}
	m.InitVersion(Tag, Commit, BuildTime)
	m.Run()
}
