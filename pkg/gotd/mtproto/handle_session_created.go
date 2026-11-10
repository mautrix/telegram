package mtproto

import (
	"time"

	"github.com/go-faster/errors"
	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mt"
)

func (c *Conn) handleSessionCreated(msgID int64, b *bin.Buffer) error {
	var s mt.NewSessionCreated
	if err := s.Decode(b); err != nil {
		return errors.Wrap(err, "decode")
	}
	c.gotSession.Signal()

	created := time.Unix(msgID>>32, int64(uint32(msgID))*int64(time.Second)>>32)
	now := c.clock.Now()
	hasServerTimeOffset := c.hasServerTimeOffset()
	c.log.Debug("Session created",
		zap.Int64("unique_id", s.UniqueID),
		zap.Int64("first_msg_id", s.FirstMsgID),
		zap.Time("server_time", created.Local()),
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
