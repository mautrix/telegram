package tdsync

import (
	"context"
	"testing"

	"github.com/go-faster/errors"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"go.uber.org/zap/zaptest"

	"go.mau.fi/mautrix-telegram/pkg/gotd/clock"
)

func TestLogGroup(t *testing.T) {
	hook := func(e zapcore.Entry) error {
		require.Contains(t, e.LoggerName, "group")
		return nil
	}
	log := zaptest.NewLogger(t, zaptest.WrapOptions(zap.Hooks(hook)))
	grp := NewLogGroup(context.Background(), log.Named("group"))
	grp.SetClock(clock.System)

	grp.Go("test-task", func(groupCtx context.Context) error {
		<-groupCtx.Done()
		return groupCtx.Err()
	})

	grp.Go("test-task2", func(groupCtx context.Context) error {
		<-groupCtx.Done()
		return nil
	})

	grp.Cancel()
	if err := grp.Wait(); !errors.Is(err, context.Canceled) {
		t.Error(err)
	}
}
