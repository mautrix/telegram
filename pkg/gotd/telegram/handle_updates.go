package telegram

import (
	"fmt"

	"github.com/go-faster/errors"
	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func (c *Client) updateInterceptor(updates ...tg.UpdateClass) {
	for _, update := range updates {
		switch update.(type) {
		case *tg.UpdateConfig, *tg.UpdateDCOptions:
			c.fetchConfig(c.ctx)
		}
	}
}

func (c *Client) processUpdates(updates tg.UpdatesClass) error {
	switch u := updates.(type) {
	case *tg.Updates:
		c.updateInterceptor(u.Updates...)
		return c.updateHandler.Handle(c.ctx, u)
	case *tg.UpdatesCombined:
		c.updateInterceptor(u.Updates...)
		return c.updateHandler.Handle(c.ctx, u)
	case *tg.UpdateShort:
		c.updateInterceptor(u.Update)
		return c.updateHandler.Handle(c.ctx, u)
	case *tg.UpdateShortMessage, *tg.UpdateShortChatMessage, *tg.UpdateShortSentMessage, *tg.UpdatesTooLong:
		return c.updateHandler.Handle(c.ctx, u)
	default:
		c.log.Warn("Ignoring update", zap.String("update_type", fmt.Sprintf("%T", u)))
	}
	return nil
}

func (c *Client) handleUpdates(b *bin.Buffer) error {
	updates, err := tg.DecodeUpdates(b)
	if err != nil {
		return errors.Wrap(err, "decode updates")
	}
	return c.processUpdates(updates)
}
