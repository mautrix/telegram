package inline

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestGame(t *testing.T) {
	ctx := context.Background()
	builder, mock := testBuilder(t)
	gameName := "game"

	mock.ExpectFunc(func(b bin.Encoder) {
		v, ok := b.(*tg.MessagesSetInlineBotResultsRequest)
		require.True(t, ok)
		require.Equal(t, int64(10), v.QueryID)

		for i := range v.Results {
			r, ok := v.Results[i].(*tg.InputBotInlineResultGame)
			require.True(t, ok)
			require.NotZero(t, r.ID)
			require.Equal(t, gameName, r.ShortName)
		}
	}).ThenTrue()
	_, err := builder.Set(ctx,
		Game(gameName, MessageText("game")),
		Game(gameName, MessageText("game")).ID("10"),
	)
	require.NoError(t, err)

	mock.Expect().ThenRPCErr(testRPCError())
	_, err = builder.Set(ctx,
		Game(gameName, MessageText("game")),
		Game(gameName, MessageText("game")).ID("10"),
	)
	require.Error(t, err)
}
