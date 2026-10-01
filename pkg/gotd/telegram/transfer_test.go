package telegram

import (
	"bytes"
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/internal/manager"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgmock"
)

func TestTransfer(t *testing.T) {
	ctx := context.Background()

	dc := 1
	mockClient(func(a *tgmock.Mock, client *Client) {
		user := &tg.User{ID: 10, Username: "abc10"}
		auth := &tg.AuthAuthorization{
			User: user,
		}
		exported := bytes.Repeat([]byte{10}, 10)
		a.ExpectCall(&tg.AuthExportAuthorizationRequest{
			DCID: dc,
		}).ThenResult(&tg.AuthExportedAuthorization{
			ID:    user.ID,
			Bytes: exported,
		}).ExpectCall(&tg.AuthImportAuthorizationRequest{
			ID:    user.ID,
			Bytes: exported,
		}).ThenResult(&tg.AuthAuthorization{
			User: user,
		})

		r, err := client.transfer(ctx, tg.NewClient(client), dc)
		require.NoError(t, err)
		require.Equal(t, auth, r)
	})(t)
}

func TestTransferWithInvokeContextDC(t *testing.T) {
	ctx := context.Background()

	dc := 1
	targetDC := dc
	ctx = context.WithValue(ctx, InvokeContextKeyDC, &targetDC)

	mockClient(func(a *tgmock.Mock, client *Client) {
		client.cfg = manager.NewAtomicConfig(tg.Config{
			ThisDC:    1,
			DCOptions: []tg.DCOption{{ID: 1, IPAddress: "127.0.0.1"}},
		})
		user := &tg.User{ID: 10, Username: "abc10"}
		exported := bytes.Repeat([]byte{10}, 10)
		a.ExpectCall(&tg.AuthExportAuthorizationRequest{
			DCID: dc,
		}).ThenResult(&tg.AuthExportedAuthorization{
			ID:    user.ID,
			Bytes: exported,
		}).ExpectCall(&tg.AuthImportAuthorizationRequest{
			ID:    user.ID,
			Bytes: exported,
		}).ThenResult(&tg.AuthAuthorization{
			User: user,
		})

		done := make(chan error, 1)
		go func() {
			_, err := client.transfer(ctx, tg.NewClient(client), dc)
			done <- err
		}()
		select {
		case err := <-done:
			require.NoError(t, err)
		case <-time.After(2 * time.Second):
			t.Fatal("transfer deadlocked on subConnsMux (re-entrant invokeSub)")
		}
	})(t)
}
