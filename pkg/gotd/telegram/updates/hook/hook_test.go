package hook

import (
	"context"
	"testing"

	"github.com/go-faster/errors"

	"github.com/stretchr/testify/assert"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestUpdateHook_InvokeRaw(t *testing.T) {
	t.Run("Success", func(t *testing.T) {
		var invokerCalled, hookCalled bool
		assert.NoError(t, UpdateHook(func(ctx context.Context, u tg.UpdatesClass) error {
			assert.NotNil(t, u)
			hookCalled = true
			return nil
		}).Handle(telegram.InvokeFunc(func(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
			invokerCalled = true
			return nil
		})).Invoke(context.TODO(), nil, &tg.UpdatesBox{
			Updates: &tg.UpdateShortMessage{
				ID: 100,
			},
		}))

		assert.True(t, invokerCalled, "invoker should be called")
		assert.True(t, hookCalled, "hook should be called")
	})
	t.Run("Error", func(t *testing.T) {
		t.Run("Handler", func(t *testing.T) {
			var invokerCalled, hookCalled bool
			err := errors.New("failure")
			assert.ErrorIs(t, UpdateHook(func(ctx context.Context, u tg.UpdatesClass) error {
				assert.NotNil(t, u)
				hookCalled = true
				return nil
			}).Handle(telegram.InvokeFunc(func(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
				invokerCalled = true
				return err
			})).Invoke(context.TODO(), nil, &tg.UpdatesBox{
				Updates: &tg.UpdateShortMessage{
					ID: 100,
				},
			}), err)

			assert.True(t, invokerCalled, "invoker should be called")
			assert.False(t, hookCalled, "hook should not be called")
		})
		t.Run("Hook", func(t *testing.T) {
			var invokerCalled, hookCalled bool
			err := errors.New("failure")
			assert.ErrorIs(t, UpdateHook(func(ctx context.Context, u tg.UpdatesClass) error {
				assert.NotNil(t, u)
				hookCalled = true
				return err
			}).Handle(telegram.InvokeFunc(func(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
				invokerCalled = true
				return nil
			})).Invoke(context.TODO(), nil, &tg.UpdatesBox{
				Updates: &tg.UpdateShortMessage{
					ID: 100,
				},
			}), err)

			assert.True(t, invokerCalled, "invoker should be called")
			assert.True(t, hookCalled, "hook should be called")
		})
	})
	t.Run("Not update", func(t *testing.T) {
		var invokerCalled, hookCalled bool
		assert.NoError(t, UpdateHook(func(ctx context.Context, u tg.UpdatesClass) error {
			assert.NotNil(t, u)
			hookCalled = true
			return nil
		}).Handle(telegram.InvokeFunc(func(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
			invokerCalled = true
			return nil
		})).Invoke(context.TODO(), nil, &tg.User{}))

		assert.True(t, invokerCalled, "invoker should be called")
		assert.False(t, hookCalled, "hook should not be called")
	})
}
