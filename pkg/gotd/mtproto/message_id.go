package mtproto

import "go.mau.fi/mautrix-telegram/pkg/gotd/proto"

func (c *Conn) newMessageID() int64 {
	return c.messageID.New(proto.MessageFromClient)
}
