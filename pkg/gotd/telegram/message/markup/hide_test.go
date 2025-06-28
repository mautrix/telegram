package markup

import (
	"testing"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestHide(t *testing.T) {
	v, ok := Hide().(*tg.ReplyKeyboardHide)
	require.True(t, ok)
	require.False(t, v.Selective)

	v, ok = SelectiveHide().(*tg.ReplyKeyboardHide)
	require.True(t, ok)
	require.True(t, v.Selective)
}
