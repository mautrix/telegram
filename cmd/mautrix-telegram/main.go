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
	"context"
	"encoding/base64"
	"fmt"
	"net/http"
	"strings"

	"github.com/gorilla/mux"
	"go.mau.fi/util/dbutil/litestream"
	"go.mau.fi/util/exerrors"
	"maunium.net/go/mautrix/bridgev2/bridgeconfig"
	"maunium.net/go/mautrix/bridgev2/matrix/mxmain"
	"maunium.net/go/mautrix/id"

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

var c = &connector.TelegramConnector{Config: &connector.TelegramConfig{}}
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
	m.PostStart = func() {
		if m.Matrix.Provisioning != nil {
			m.Matrix.Provisioning.GetAuthFromRequest = func(r *http.Request) string {
				if !strings.HasSuffix(r.URL.Path, "/login/qr") {
					return ""
				}
				authParts := strings.Split(r.Header.Get("Sec-WebSocket-Protocol"), ",")
				for _, part := range authParts {
					part = strings.TrimSpace(part)
					if strings.HasPrefix(part, "net.maunium.telegram.auth-") {
						return strings.TrimPrefix(part, "net.maunium.telegram.auth-")
					}
				}
				return ""
			}
			m.Matrix.Provisioning.GetUserIDFromRequest = func(r *http.Request) id.UserID {
				return id.UserID(mux.Vars(r)["userID"])
			}
			m.Matrix.Provisioning.Router.HandleFunc("/v1/user/{userID}/login/qr", legacyProvLoginQR)
			m.Matrix.Provisioning.Router.HandleFunc("/v1/user/{userID}/login/request_code", legacyProvLoginRequestCode)
			m.Matrix.Provisioning.Router.HandleFunc("/v1/user/{userID}/login/send_code", legacyProvLoginSendCode)
			m.Matrix.Provisioning.Router.HandleFunc("/v1/user/{userID}/login/send_password", legacyProvLoginSendPassword)
			m.Matrix.Provisioning.Router.HandleFunc("/v1/user/{userID}/logout", legacyProvLogout)
			m.Matrix.Provisioning.Router.HandleFunc("/v1/user/{userID}/contacts", legacyProvContacts)
			m.Matrix.Provisioning.Router.HandleFunc("/v1/user/{userID}/resolve_identifier/{identifier}", legacyProvResolveIdentifier)
			m.Matrix.Provisioning.Router.HandleFunc("/v1/user/{userID}/pm/{identifier}", legacyProvPM)
		}
	}
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
		ctx := context.TODO()
		if exists, _ := m.DB.TableExists(ctx, "telegram_file_old"); exists {
			exerrors.Must(m.DB.Exec(ctx, `
				PRAGMA foreign_keys = 'OFF';
				DROP TABLE telegram_file_old;
				PRAGMA foreign_key_check;
				PRAGMA foreign_keys = 'ON';
			`))
		}
	}
	m.InitVersion(Tag, Commit, BuildTime)
	m.Run()
}
