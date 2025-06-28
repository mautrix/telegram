package tgmock

import (
	"context"
	"crypto/rand"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/crypto"
)

// Invoke implements tg.Invoker.
func (i *Mock) Invoke(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
	h := i.Handler()

	id, err := crypto.RandInt64(rand.Reader)
	if err != nil {
		return errors.Wrap(err, "generate id")
	}

	body, err := h(id, input)
	if err != nil {
		return errors.Wrap(err, "mock invoke")
	}

	buf := new(bin.Buffer)
	if err := body.Encode(buf); err != nil {
		return errors.Wrap(err, "encode")
	}
	if err := output.Decode(buf); err != nil {
		return errors.Wrap(err, "decode")
	}
	return nil
}
