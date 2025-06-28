package message

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestBuilder(t *testing.T) {
	a := require.New(t)
	b := new(Builder)

	b = b.Silent()
	a.True(b.silent)

	b = b.Background()
	a.True(b.background)

	b = b.Clear()
	a.True(b.clearDraft)

	b = b.NoWebpage()
	a.True(b.noWebpage)

	b = b.ReplyMsg(&tg.Message{ID: 10})
	a.Equal(10, b.replyTo.(*tg.InputReplyToMessage).ReplyToMsgID)

	date := time.Now()
	b = b.Schedule(date)
	a.Equal(int(date.Unix()), b.scheduleDate)

	markup := &tg.ReplyInlineMarkup{}
	b = b.Markup(markup)
	a.Equal(markup, b.replyMarkup)
}
