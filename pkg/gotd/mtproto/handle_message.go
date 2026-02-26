package mtproto

import (
	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mt"
	"go.mau.fi/mautrix-telegram/pkg/gotd/proto"
)

func (c *Conn) handleMessage(msgID int64, b *bin.Buffer) error {
	id, err := b.PeekID()
	if err != nil {
		// Empty body.
		return errors.Wrap(err, "peek message type")
	}

	switch id {
	case mt.NewSessionCreatedTypeID:
		return c.handleSessionCreated(b)
	case mt.BadMsgNotificationTypeID, mt.BadServerSaltTypeID:
		return c.handleBadMsg(msgID, b)
	case mt.FutureSaltsTypeID:
		return c.handleFutureSalts(b)
	case proto.MessageContainerTypeID:
		return c.handleContainer(msgID, b)
	case proto.ResultTypeID:
		return c.handleResult(b)
	case mt.PongTypeID:
		return c.handlePong(b)
	case mt.MsgsAckTypeID:
		return c.handleAck(b)
	case proto.GZIPTypeID:
		return c.handleGZIP(msgID, b)
	case mt.MsgDetailedInfoTypeID,
		mt.MsgNewDetailedInfoTypeID:
		return nil
	default:
		return c.handler.OnMessage(b)
	}
}
