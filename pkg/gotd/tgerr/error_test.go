package tgerr_test

import (
	"testing"

	"github.com/go-faster/errors"
	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/testutil"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

func TestError(t *testing.T) {
	t.Run("FLOOD_WAIT_0", func(t *testing.T) {
		require.Equal(t, "rpc error code 420: FLOOD_WAIT (0)", tgerr.New(420, "FLOOD_WAIT_0").Error())
	})
	t.Run("FLOOD_WAIT", func(t *testing.T) {
		require.Equal(t, "rpc error code 420: FLOOD_WAIT", tgerr.New(420, "FLOOD_WAIT").Error())
	})
}

func TestErrorParse(t *testing.T) {
	t.Run("FLOOD_WAIT", func(t *testing.T) {
		require.Equal(t, &tgerr.Error{
			Code:     420,
			Message:  "FLOOD_WAIT_359",
			Type:     "FLOOD_WAIT",
			Argument: 359,
		}, tgerr.New(420, "FLOOD_WAIT_359"))
	})
	t.Run("FLOOD_WAIT_0", func(t *testing.T) {
		require.Equal(t, &tgerr.Error{
			Code:     420,
			Message:  "FLOOD_WAIT_0",
			Type:     "FLOOD_WAIT",
			Argument: 0,
		}, tgerr.New(420, "FLOOD_WAIT_0"))
	})
	t.Run("Middle", func(t *testing.T) {
		require.Equal(t, &tgerr.Error{
			Code:     169,
			Message:  "GO_1337_METERS_AWAY",
			Type:     "GO_METERS_AWAY",
			Argument: 1337,
		}, tgerr.New(169, "GO_1337_METERS_AWAY"))
	})
}

func TestHelpers(t *testing.T) {
	err := func() error {
		return tgerr.New(169, "GO_1337_METERS_AWAY")
	}()
	t.Run("Type", func(t *testing.T) {
		require.True(t, tgerr.Is(err, "GO_METERS_AWAY"))
		require.True(t, tgerr.Is(err, "FOO", "GO_METERS_AWAY"))
		require.False(t, tgerr.Is(err, "NOPE"))
		t.Run("AsType", func(t *testing.T) {
			{
				rpcErr, ok := tgerr.AsType(err, "NOPE")
				require.False(t, ok)
				require.Nil(t, rpcErr)
			}
			{
				rpcErr, ok := tgerr.AsType(err, "GO_METERS_AWAY")
				require.True(t, ok)
				require.NotNil(t, rpcErr)
			}
		})
	})
	t.Run("Code", func(t *testing.T) {
		require.True(t, tgerr.IsCode(err, 169))
		require.True(t, tgerr.IsCode(err, 1, 169))
		require.False(t, tgerr.IsCode(err, 168))
	})
	t.Run("Generated", func(t *testing.T) {
		// Ensure that code generation works for errors.
		err := func() error {
			rpcErr := &tgerr.Error{
				Type: tg.ErrAccessTokenExpired,
			}

			return errors.Wrap(rpcErr, "perform operation")
		}()
		require.True(t, tgerr.Is(err, tg.ErrAccessTokenExpired))
		require.True(t, tg.IsAccessTokenExpired(err))
	})
	t.Run("ErrorType", func(t *testing.T) {
		tests := []struct {
			name  string
			value error
		}{
			{"Nil", nil},
			{"WrongType", testutil.TestError()},
		}
		for _, tt := range tests {
			t.Run(tt.name, func(t *testing.T) {
				a := require.New(t)
				e := tt.value

				_, ok := tgerr.As(e)
				a.False(ok)

				_, ok = tgerr.AsType(e, "")
				a.False(ok)

				_, ok = tgerr.AsFloodWait(e)
				a.False(ok)

				a.False(tgerr.Is(e, ""))
				a.False(tgerr.IsCode(e, 0))
			})
		}
	})
}
