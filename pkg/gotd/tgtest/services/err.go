package services

import (
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgtest"
)

var (
	// ErrMethodNotImplemented denotes that method is not implemented.
	ErrMethodNotImplemented error = tgerr.New(400, "INPUT_METHOD_INVALID")

	// NotImplemented is a simple handler which returns ErrMethodNotImplemented.
	NotImplemented tgtest.HandlerFunc = func(server *tgtest.Server, req *tgtest.Request) error {
		return ErrMethodNotImplemented
	}
)
