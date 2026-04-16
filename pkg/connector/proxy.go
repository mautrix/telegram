// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 Vladislav Agarkov
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

	"golang.org/x/net/proxy"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/dcs"
)

func GetProxyDialFunc(cfg ProxyConfig) (dcs.DialFunc, error) {
	switch cfg.Type {
	// we can't proxy HTTP through mtproxy
	case "disabled", "mtproxy":
		return nil, nil
	case "socks5":
		var auth *proxy.Auth
		if cfg.Username != "" && cfg.Password != "" {
			auth = &proxy.Auth{User: cfg.Username, Password: cfg.Password}
		}
		sock5, err := proxy.SOCKS5("tcp", cfg.Address, auth, proxy.Direct)
		if err != nil {
			return nil, err
		}
		return sock5.(proxy.ContextDialer).DialContext, nil
	default:
		return nil, fmt.Errorf("unsupported proxy type %s", cfg.Type)
	}
}

func GetProxyResolver(cfg ProxyConfig) (dcs.Resolver, error) {
	switch cfg.Type {
	case "disabled", "socks5":
		dialer, err := GetProxyDialFunc(cfg)
		if err != nil {
			return nil, err
		}
		resolver := dcs.Plain(dcs.PlainOptions{Dial: dialer})
		return resolver, nil
	case "mtproxy":
		return dcs.MTProxy(cfg.Address, []byte(cfg.Password), dcs.MTProxyOptions{})
	default:
		return nil, fmt.Errorf("unsupported proxy type %s", cfg.Type)
	}
}
