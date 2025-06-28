package telegram

import (
	"go.mau.fi/mautrix-telegram/pkg/gotd/session"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

// SessionStorage is alias of mtproto.SessionStorage.
type SessionStorage = session.Storage

// FileSessionStorage is alias of mtproto.FileSessionStorage.
type FileSessionStorage = session.FileStorage

// Error represents RPC error returned to request.
type Error = tgerr.Error
