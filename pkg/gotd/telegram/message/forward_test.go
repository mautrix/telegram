package message

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestBuilder_ForwardIDs(t *testing.T) {
	ctx := context.Background()
	sender, mock := testSender(t)

	mock.ExpectFunc(func(b bin.Encoder) {
		req, ok := b.(*tg.MessagesForwardMessagesRequest)
		require.True(t, ok)
		require.Equal(t, &tg.InputPeerSelf{}, req.ToPeer)
		require.Equal(t, &tg.InputPeerSelf{}, req.FromPeer)
		require.Len(t, req.ID, 1)
		require.Equal(t, 10, req.ID[0])
		require.True(t, req.WithMyScore)
	}).ThenResult(&tg.Updates{})
	_, err := sender.Self().ForwardIDs(&tg.InputPeerSelf{}, 10).WithMyScore().Send(ctx)
	require.NoError(t, err)

	mock.ExpectFunc(func(b bin.Encoder) {
		req, ok := b.(*tg.MessagesForwardMessagesRequest)
		require.True(t, ok)
		require.Equal(t, &tg.InputPeerSelf{}, req.ToPeer)
		require.Equal(t, &tg.InputPeerSelf{}, req.FromPeer)
		require.Len(t, req.ID, 1)
		require.Equal(t, 10, req.ID[0])
		require.True(t, req.WithMyScore)
	}).ThenRPCErr(testRPCError())
	_, err = sender.Self().ForwardIDs(&tg.InputPeerSelf{}, 10).WithMyScore().Send(ctx)
	require.Error(t, err)
}
