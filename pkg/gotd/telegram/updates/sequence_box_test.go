package updates

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
	"go.uber.org/zap/zaptest"
)

func TestSequenceBox(t *testing.T) {
	var (
		state   int
		updates []update
	)

	ctx := context.Background()
	box := newSequenceBox(sequenceConfig{
		InitialState: 3,
		Apply: func(ctx context.Context, s int, u []update) error {
			state = s
			updates = append(updates, u...)
			return nil
		},
		Logger: zaptest.NewLogger(t),
	})

	require.Nil(t, box.Handle(ctx, update{
		Value: 1,
		State: 2,
		Count: 1,
	}))
	require.Zero(t, state)
	require.Empty(t, updates)
	require.Empty(t, box.pending)

	require.Nil(t, box.Handle(ctx, update{
		Value: 1,
		State: 3,
		Count: 1,
	}))
	require.Zero(t, state)
	require.Empty(t, updates)
	require.Empty(t, box.pending)

	require.Nil(t, box.Handle(ctx, update{
		Value: 1,
		State: 4,
		Count: 1,
	}))
	require.Equal(t, 4, state)
	require.Equal(t, []update{{Value: 1, State: 4, Count: 1}}, updates)
	require.Empty(t, box.pending)
	updates = nil

	require.Nil(t, box.Handle(ctx, update{
		Value: 1,
		State: 6,
		Count: 1,
	}))
	require.Equal(t, 4, state)
	require.Empty(t, updates)
	require.Equal(t, []update{{Value: 1, State: 6, Count: 1}}, box.pending)

	require.Nil(t, box.Handle(ctx, update{
		Value: 2,
		State: 5,
		Count: 1,
	}))
	require.Equal(t, 6, state)
	require.Equal(t, []update{{Value: 2, State: 5, Count: 1}, {Value: 1, State: 6, Count: 1}}, updates)
	require.Empty(t, box.pending)
	updates = nil

	require.Nil(t, box.Handle(ctx, update{
		Value: 3,
		State: 8,
		Count: 1,
	}))
	require.Equal(t, 6, state)
	require.Empty(t, updates)
	require.Equal(t, []update{{Value: 3, State: 8, Count: 1}}, box.pending)
	<-box.gapTimeout.C

	require.Equal(t, []gap{{from: 6, to: 7}}, box.gaps.gaps)
	box.gaps.Clear()
	require.False(t, box.gaps.Has())
}

func TestSequenceBoxTrimStaleOnStateJump(t *testing.T) {
	ctx := context.Background()
	box := newSequenceBox(sequenceConfig{
		InitialState: 5,
		Apply: func(ctx context.Context, s int, u []update) error {
			return nil
		},
		Logger: zaptest.NewLogger(t),
	})

	// Diff update arrives ahead of the current state and is parked.
	require.Nil(t, box.Handle(ctx, update{Value: "payload", State: 8, Count: 1}))
	require.Equal(t, []update{{Value: "payload", State: 8, Count: 1}}, box.pending)

	// getDifference: gap is cleared and state jumps past the parked update.
	require.Equal(t, []gap{{from: 5, to: 7}}, box.gaps.gaps)
	box.gaps.Clear()
	require.False(t, box.gaps.Has())
	box.SetState(9, "updates.channelDifference")

	// The outdated update must be released even though no further
	// contiguous update ever arrives for the quiet channel.
	require.Empty(t, box.pending)

	// A pending update that is still in the future must be kept.
	require.Nil(t, box.Handle(ctx, update{Value: "future", State: 11, Count: 1}))
	box.gaps.Clear()
	box.SetState(10, "updates.channelDifference")
	require.Equal(t, []update{{Value: "future", State: 11, Count: 1}}, box.pending)
	// A future update must survive the trim and be applied without panic,
	// advancing the state instead of clobbering it to 0.
	require.Nil(t, box.applyPending(ctx))
	require.Equal(t, 11, box.State())
}

func TestSequenceBoxTrimStaleOnOutdatedUpdate(t *testing.T) {
	ctx := context.Background()
	box := newSequenceBox(sequenceConfig{
		InitialState: 5,
		Apply: func(ctx context.Context, s int, u []update) error {
			return nil
		},
		Logger: zaptest.NewLogger(t),
	})

	require.Nil(t, box.Handle(ctx, update{Value: "payload", State: 8, Count: 1}))
	require.Equal(t, []update{{Value: "payload", State: 8, Count: 1}}, box.pending)

	// An outdated update arrives without any state jump; the parked
	// update is still in the future, so it must be kept.
	require.Nil(t, box.Handle(ctx, update{Value: 1, State: 3, Count: 1}))
	require.Equal(t, []update{{Value: "payload", State: 8, Count: 1}}, box.pending)

	// A further state jump makes the parked update outdated.
	box.SetState(9, "updates.channelDifference")
	require.Empty(t, box.pending)

	// Subsequent outdated updates keep trimming.
	require.Nil(t, box.Handle(ctx, update{Value: 2, State: 2, Count: 1}))
	require.Empty(t, box.pending)
}

func TestSequenceBoxApplyPending(t *testing.T) {
	tests := []struct {
		InitialState int
		Pending      []update
		PendingAfter []update
		Applied      []update
	}{
		{
			InitialState: 5,
			Pending: []update{
				{Value: 1, State: 3, Count: 1},
				{Value: 1, State: 4, Count: 1},
				{Value: 1, State: 1, Count: 1},
			},
			PendingAfter: []update{},
			Applied:      []update{},
		},
		{
			InitialState: 5,
			Pending: []update{
				{Value: 1, State: 3, Count: 1},
				{Value: 1, State: 8, Count: 1},
				{Value: 1, State: 7, Count: 1},
				{Value: 1, State: 4, Count: 1},
				{Value: 1, State: 1, Count: 1},
			},
			PendingAfter: []update{
				{1, 7, 1, entities{}},
				{1, 8, 1, entities{}},
			},
			Applied: []update{},
		},
		{
			InitialState: 5,
			Pending: []update{
				{Value: 1, State: 8, Count: 1},
				{Value: 1, State: 7, Count: 1},
			},
			PendingAfter: []update{
				{1, 7, 1, entities{}},
				{Value: 1, State: 8, Count: 1},
			},
			Applied: []update{},
		},
		{
			InitialState: 5,
			Pending: []update{
				{Value: 1, State: 3, Count: 1},
				{Value: 1, State: 6, Count: 1},
				{Value: 1, State: 8, Count: 1},
				{Value: 1, State: 4, Count: 1},
				{Value: 1, State: 1, Count: 1},
			},
			PendingAfter: []update{
				{Value: 1, State: 8, Count: 1},
			},
			Applied: []update{
				{Value: 1, State: 6, Count: 1},
			},
		},
	}

	for _, test := range tests {
		applied := make([]update, 0)
		box := newSequenceBox(sequenceConfig{
			InitialState: test.InitialState,
			Apply: func(_ context.Context, s int, u []update) error {
				applied = append(applied, u...)
				return nil
			},
			Logger: zaptest.NewLogger(t),
		})

		box.pending = test.Pending
		require.NoError(t, box.applyPending(context.TODO()))
		require.Equal(t, test.PendingAfter, box.pending)
		require.Equal(t, test.Applied, applied)
	}
}
