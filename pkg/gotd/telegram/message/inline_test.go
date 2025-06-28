package message

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestBuilder_InlineResult(t *testing.T) {
	ctx := context.Background()
	sender, mock := testSender(t)

	mock.ExpectFunc(func(b bin.Encoder) {
		req, ok := b.(*tg.MessagesSendInlineBotResultRequest)
		require.True(t, ok)
		require.Equal(t, &tg.InputPeerSelf{}, req.Peer)
		require.Equal(t, int64(10), req.QueryID)
		require.Equal(t, "10", req.ID)
		require.True(t, req.HideVia)
	}).ThenResult(&tg.Updates{})
	_, err := sender.Self().InlineResult(ctx, "10", 10, true)
	require.NoError(t, err)

	mock.ExpectFunc(func(b bin.Encoder) {
		req, ok := b.(*tg.MessagesSendInlineBotResultRequest)
		require.True(t, ok)
		require.Equal(t, &tg.InputPeerSelf{}, req.Peer)
		require.Equal(t, int64(10), req.QueryID)
		require.Equal(t, "10", req.ID)
		require.False(t, req.HideVia)
	}).ThenRPCErr(testRPCError())
	_, err = sender.Self().InlineResult(ctx, "10", 10, false)
	require.Error(t, err)
}
