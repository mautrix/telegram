package mtproto

import (
	"github.com/go-faster/errors"
	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mt"
	"go.mau.fi/mautrix-telegram/pkg/gotd/proto"
)

func (c *Conn) handleSessionCreated(b *bin.Buffer) error {
	var s mt.NewSessionCreated
	if err := s.Decode(b); err != nil {
		return errors.Wrap(err, "decode")
	}
	c.gotSession.Signal()

	created := proto.MessageID(s.FirstMsgID).Time()
	now := c.clock.Now()
	hasServerTimeOffset := c.hasServerTimeOffset()
	c.log.Debug("Session created",
		zap.Int64("unique_id", s.UniqueID),
		zap.Int64("first_msg_id", s.FirstMsgID),
		zap.Time("first_msg_time", created.Local()),
		zap.Bool("has_server_time_offset", hasServerTimeOffset),
	)

	if !hasServerTimeOffset {
		c.setServerTimeOffset(created.Sub(now))
	}
	c.storeSalt(s.ServerSalt)
	if err := c.handler.OnSession(c.session()); err != nil {
		return errors.Wrap(err, "handler.OnSession")
	}
	return nil
}
