package message

import (
	"context"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message/inline"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

// InlineResult is a user method to send bot inline query result message.
func (b *Builder) InlineResult(ctx context.Context, id string, queryID int64, hideVia bool) (tg.UpdatesClass, error) {
	p, err := b.peer(ctx)
	if err != nil {
		return nil, errors.Wrap(err, "peer")
	}

	upd, err := b.sender.sendInlineBotResult(ctx, &tg.MessagesSendInlineBotResultRequest{
		Silent:       b.silent,
		Background:   b.background,
		ClearDraft:   b.clearDraft,
		HideVia:      hideVia,
		Peer:         p,
		ReplyTo:      b.replyTo,
		QueryID:      queryID,
		ID:           id,
		ScheduleDate: b.scheduleDate,
	})
	if err != nil {
		return nil, errors.Wrap(err, "send inline bot result")
	}

	return upd, nil
}

// InlineUpdate is an abstraction for
type InlineUpdate interface {
	GetQueryID() int64
}

// Inline creates new inline.ResultBuilder using given update.
func (s *Sender) Inline(upd InlineUpdate) *inline.ResultBuilder {
	return inline.New(s.raw, s.rand, upd.GetQueryID())
}
