package tgmock

import "go.mau.fi/mautrix-telegram/pkg/gotd/bin"

// Handler is a RPC call handler.
type Handler interface {
	Handle(id int64, body bin.Encoder) (bin.Encoder, error)
}

// HandlerFunc is a function adapter for Handler.
type HandlerFunc func(id int64, body bin.Encoder) (bin.Encoder, error)

// Handle implements Handler.
func (h HandlerFunc) Handle(id int64, body bin.Encoder) (bin.Encoder, error) {
	return h(id, body)
}
