package manager

import (
	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mtproto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

// Handler abstracts updates and session handler.
type Handler interface {
	OnSession(cfg tg.Config, s mtproto.Session) error
	OnMessage(b *bin.Buffer) error
}

// NoopHandler is a noop handler.
type NoopHandler struct{}

// OnSession implements Handler.
func (n NoopHandler) OnSession(cfg tg.Config, s mtproto.Session) error {
	return nil
}

// OnMessage implements Handler
func (n NoopHandler) OnMessage(b *bin.Buffer) error {
	return nil
}
