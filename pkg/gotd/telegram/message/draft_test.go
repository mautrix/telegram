package message

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message/styling"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestDraft(t *testing.T) {
	ctx := context.Background()
	sender, mock := testSender(t)

	mock.ExpectCall(&tg.MessagesSaveDraftRequest{
		Peer:    &tg.InputPeerSelf{},
		Message: "text",
	}).ThenTrue()
	mock.ExpectCall(&tg.MessagesSaveDraftRequest{
		Peer:    &tg.InputPeerSelf{},
		Message: "styled text",
		Entities: []tg.MessageEntityClass{
			&tg.MessageEntityBold{
				Length: len("styled text"),
			},
		},
	}).ThenTrue()
	mock.ExpectCall(&tg.MessagesSaveDraftRequest{
		Peer: &tg.InputPeerSelf{},
	}).ThenTrue()

	require.NoError(t, sender.Self().SaveDraft(ctx, "text"))
	require.NoError(t, sender.Self().SaveStyledDraft(ctx, styling.Bold("styled text")))
	require.NoError(t, sender.Self().ClearDraft(ctx))
}
