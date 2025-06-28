package exchange

import (
	"context"
	"crypto/rsa"
	"math/rand"
	"net"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"go.uber.org/zap/zaptest"

	"go.mau.fi/mautrix-telegram/pkg/gotd/crypto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tdsync"
	"go.mau.fi/mautrix-telegram/pkg/gotd/transport"
)

func TestExchangeTimeout(t *testing.T) {
	a := require.New(t)

	reader := rand.New(rand.NewSource(1))
	key, err := rsa.GenerateKey(reader, crypto.RSAKeyBits)
	a.NoError(err)
	log := zaptest.NewLogger(t)

	i := transport.Intermediate
	client, _ := i.Pipe()

	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()

	g := tdsync.NewCancellableGroup(ctx)
	g.Go(func(ctx context.Context) error {
		_, err := NewExchanger(client, 2).
			WithLogger(log.Named("client")).
			WithRand(reader).
			WithTimeout(1 * time.Second).
			Client([]PublicKey{
				{
					RSA: &key.PublicKey,
				},
			}).
			Run(ctx)
		return err
	})

	err = g.Wait()
	var e net.Error
	a.ErrorAs(err, &e)
	a.True(e.Timeout())
}
