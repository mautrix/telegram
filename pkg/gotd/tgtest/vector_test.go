package tgtest

import (
	"testing"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/testutil"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

type badEncoder struct{}

func (e badEncoder) Encode(b *bin.Buffer) error {
	return testutil.TestError()
}

func TestGenericVector_Encode(t *testing.T) {
	tests := []struct {
		name    string
		data    []bin.Encoder
		expect  bin.Decoder
		wantErr bool
	}{
		{"Empty", nil, nil, false},
		{"Nil", []bin.Encoder{nil}, nil, true},
		{"BadObject", []bin.Encoder{badEncoder{}}, nil, true},
		{
			"Plain",
			[]bin.Encoder{&tg.BotCommand{
				Command:     "hello",
				Description: "world",
			}},
			&tg.BotCommandVector{},
			false,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var (
				a   = require.New(t)
				v   = genericVector{Elems: test.data}
				buf bin.Buffer
			)

			err := v.Encode(&buf)
			if test.wantErr {
				a.Error(err)
			} else if test.expect != nil {
				a.NoError(test.expect.Decode(&buf))
			}
		})
	}
}
