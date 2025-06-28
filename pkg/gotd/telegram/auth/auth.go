// Package auth provides authentication on top of tg.Client.
package auth

import (
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

// IsKeyUnregistered reports whether err is AUTH_KEY_UNREGISTERED error.
//
// Deprecated: use IsUnauthorized.
func IsKeyUnregistered(err error) bool {
	return tgerr.Is(err, "AUTH_KEY_UNREGISTERED")
}

// IsUnauthorized reports whether err is any 401 UNAUTHORIZED or is a 406
// NOT_ACCEPTABLE with AUTH_KEY_DUPLICATED.
//
// https://core.telegram.org/api/errors#401-unauthorized
// https://core.telegram.org/api/errors#406-not-acceptable
func IsUnauthorized(err error) bool {
	return tgerr.IsCode(err, 401) ||
		(tgerr.IsCode(err, 406) && tgerr.Is(err, "AUTH_KEY_DUPLICATED"))
}
