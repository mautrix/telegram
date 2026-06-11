package telegram_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"go.uber.org/zap/zaptest"

	"go.mau.fi/mautrix-telegram/pkg/gotd/session"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/dcs"
	"go.mau.fi/mautrix-telegram/pkg/gotd/testutil"
	"go.mau.fi/mautrix-telegram/pkg/gotd/transport"
)

func tryConnect(ctx context.Context, opts telegram.Options) error {
	client := telegram.NewClient(telegram.TestAppID, telegram.TestAppHash, opts)
	return client.Run(ctx, func(ctx context.Context) error {
		_, err := client.API().HelpGetNearestDC(ctx)
		return err
	})
}

func testTransportExternal(resolver dcs.Resolver, storage session.Storage) func(t *testing.T) {
	return func(t *testing.T) {
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()

		log := zaptest.NewLogger(t)
		defer func() { _ = log.Sync() }()

		require.NoError(t, tryConnect(ctx, telegram.Options{
			Logger:         log.Named("client"),
			SessionStorage: storage,
			Resolver:       resolver,
		}))
	}
}

func TestExternalE2EConnect(t *testing.T) {
	testutil.SkipExternal(t)
	// To re-use session.
	storage := &session.StorageMemory{}

	tcp := func(p dcs.Protocol) func(t *testing.T) {
		return testTransportExternal(dcs.Plain(dcs.PlainOptions{Protocol: p}), storage)
	}

	t.Run("Abridged", tcp(transport.Abridged))
	t.Run("Intermediate", tcp(transport.Intermediate))
	t.Run("PaddedIntermediate", tcp(transport.PaddedIntermediate))
	t.Run("Full", tcp(transport.Full))

	wsOpts := dcs.WebsocketOptions{}
	t.Run("Websocket", testTransportExternal(dcs.Websocket(wsOpts), storage))
}
