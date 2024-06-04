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
	"os"

	"go.mau.fi/util/dbutil"
	_ "go.mau.fi/util/dbutil/litestream"
	"go.mau.fi/util/exerrors"
	"go.mau.fi/util/exzerolog"
	"gopkg.in/yaml.v3"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/bridgeconfig"
	"maunium.net/go/mautrix/bridgev2/matrix"

	"go.mau.fi/mautrix-telegram/pkg/connector"
)

func main() {
	var cfg bridgeconfig.Config
	config := exerrors.Must(os.ReadFile("config.yaml"))
	exerrors.PanicIfNotNil(yaml.Unmarshal(config, &cfg))
	log := exerrors.Must(cfg.Logging.Compile())
	exzerolog.SetupDefaults(log)

	db := exerrors.Must(dbutil.NewFromConfig("mautrix-telegram", cfg.Database, dbutil.ZeroLogger(log.With().Str("db_section", "main").Logger())))
	telegramConnector := connector.NewConnector()
	exerrors.PanicIfNotNil(cfg.Network.Decode(telegramConnector.Config))
	bridge := bridgev2.NewBridge("", db, *log, matrix.NewConnector(&cfg), telegramConnector)
	bridge.CommandPrefix = "!telegram"
	bridge.Start()
}
