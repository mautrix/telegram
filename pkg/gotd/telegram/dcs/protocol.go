package dcs

import (
	"net"

	"go.mau.fi/mautrix-telegram/pkg/gotd/transport"
)

type protocol interface {
	Handshake(conn net.Conn) (transport.Conn, error)
}
