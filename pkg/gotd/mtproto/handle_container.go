package mtproto

import (
	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/proto"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
)

func (c *Conn) handleContainer(msgID int64, b *bin.Buffer) error {
	var container proto.MessageContainer
	if err := container.Decode(b); err != nil {
		return errors.Wrap(err, "container")
	}
	for _, msg := range container.Messages {
		if err := c.processContainerMessage(msgID, msg); err != nil {
			return err
		}
	}
	return nil
}

func (c *Conn) processContainerMessage(msgID int64, msg proto.Message) error {
	b := &bin.Buffer{Buf: msg.Body}
	return c.handleMessage(msgID, b)
}
