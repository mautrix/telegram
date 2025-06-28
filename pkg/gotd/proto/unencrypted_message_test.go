package proto

import (
	"testing"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"

	"github.com/stretchr/testify/require"
)

func TestUnencryptedMessage_Encode(t *testing.T) {
	d := UnencryptedMessage{
		MessageID:   3401235567,
		MessageData: []byte{1, 2, 3, 100, 112},
	}
	b := new(bin.Buffer)
	if err := d.Encode(b); err != nil {
		t.Fatal(err)
	}
	decoded := UnencryptedMessage{}
	if err := decoded.Decode(b); err != nil {
		t.Fatal(err)
	}
	require.Equal(t, d, decoded)
	require.Zero(t, b.Len(), "buffer should be consumed")
}
