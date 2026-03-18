package tgerr

import (
	"context"
	"time"

	"github.com/rs/zerolog"

	"go.mau.fi/mautrix-telegram/pkg/gotd/clock"
)

// ErrPremiumFloodWait is error type of "FLOOD_PREMIUM_WAIT" error.
const ErrPremiumFloodWait = "FLOOD_PREMIUM_WAIT"

// ErrFloodWait is error type of "FLOOD_WAIT" error.
const ErrFloodWait = "FLOOD_WAIT"

// FloodWaitErrors is a list of errors that are considered as flood wait.
var FloodWaitErrors = []string{ErrFloodWait, ErrPremiumFloodWait}

// AsFloodWait returns wait duration and true boolean if err is
// the "FLOOD_WAIT" error.
//
// Client should wait for that duration before issuing new requests with
// same method.
func AsFloodWait(err error) (d time.Duration, ok bool) {
	for _, e := range FloodWaitErrors {
		if rpcErr, ok := AsType(err, e); ok {
			return time.Second * time.Duration(rpcErr.Argument), true
		}
	}
	return 0, false
}

type floodWaitOptions struct {
	clock       clock.Clock
	maxDuration time.Duration
}

// FloodWaitOption configures flood wait.
type FloodWaitOption interface {
	apply(o *floodWaitOptions)
}

type floodWaitOptionFunc func(o *floodWaitOptions)

func (f floodWaitOptionFunc) apply(o *floodWaitOptions) {
	f(o)
}

// FloodWaitWithClock sets time source for flood wait.
func FloodWaitWithClock(c clock.Clock) FloodWaitOption {
	return floodWaitOptionFunc(func(o *floodWaitOptions) {
		o.clock = c
	})
}

func FloodWaitWithMaxDuration(d time.Duration) FloodWaitOption {
	return floodWaitOptionFunc(func(o *floodWaitOptions) {
		o.maxDuration = d
	})
}

// FloodWait sleeps required duration and returns true if err is FLOOD_WAIT
// or false and context or original error otherwise.
func FloodWait(ctx context.Context, err error, opts ...FloodWaitOption) (bool, error) {
	opt := &floodWaitOptions{
		clock:       clock.System,
		maxDuration: 1 * time.Hour,
	}
	for _, o := range opts {
		o.apply(opt)
	}
	if d, ok := AsFloodWait(err); ok && d < opt.maxDuration {
		timer := opt.clock.Timer(d + 1*time.Second)
		defer clock.StopTimer(timer)
		zerolog.Ctx(ctx).Warn().Dur("duration", d).Msg("Waiting on flood wait")
		select {
		case <-timer.C():
			return true, err
		case <-ctx.Done():
			return false, ctx.Err()
		}
	}

	return false, err
}
