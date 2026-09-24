package updates

import (
	"context"
	"fmt"
	"sync"

	"github.com/go-faster/errors"
	"go.opentelemetry.io/otel/trace"
	"go.uber.org/zap"
	"golang.org/x/sync/errgroup"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

var _ telegram.UpdateHandler = (*Manager)(nil)

// Manager deals with gaps.
//
// Important:
// Updates produced by this manager may contain
// negative Pts/Qts/Seq values in tg.UpdateClass/tg.UpdatesClass
// (does not affects to the tg.MessageClass).
//
// This is because telegram server does not return these sequences
// for getDifference/getChannelDifference results.
// You SHOULD NOT use them in update handlers at all.
type Manager struct {
	state *internalState
	mux   sync.Mutex

	// immutable:

	cfg    Config
	lg     *zap.Logger
	tracer trace.Tracer
}

// New creates new manager.
func New(cfg Config) *Manager {
	cfg.setDefaults()
	return &Manager{
		cfg:    cfg,
		lg:     cfg.Logger,
		tracer: cfg.TracerProvider.Tracer(""),
	}
}

// Handle handles updates.
//
// Important:
// If Run method not called, all updates will be passed
// to the provided handler as-is without any order verification
// or short updates transformation.
func (m *Manager) Handle(ctx context.Context, u tg.UpdatesClass) error {
	ctx, span := m.tracer.Start(ctx, "updates.Manager.Handle")
	defer span.End()

	m.mux.Lock()
	state := m.state
	m.mux.Unlock()

	if state == nil {
		m.lg.Debug("Handle (no internalState)")
		return m.cfg.Handler.Handle(ctx, u)
	}

	return state.Push(ctx, u)
}

type AuthOptions struct {
	IsBot   bool
	Forget  bool
	OnStart func(ctx context.Context)
}

type PtsAccessHashTuple struct {
	Pts        int
	AccessHash int64
}

func (m *Manager) checkParticipant(ctx context.Context, api API, userID, channelID, hash int64) (isMember bool, err error) {
	lg := m.lg.With(zap.Int64("channel_id", channelID))
	lg.Info("Ensuring user is still in channel")
	pcp, err := api.ChannelsGetParticipant(ctx, &tg.ChannelsGetParticipantRequest{
		Channel: &tg.InputChannel{
			ChannelID:  channelID,
			AccessHash: hash,
		},
		Participant: &tg.InputPeerSelf{},
	})
	if err != nil {
		if tgerr.Is(err, tg.ErrChannelInvalid, tg.ErrChannelPrivate, tg.ErrUserNotParticipant) {
			lg.Warn("Removing update state for channel after error", zap.Error(err))
		} else {
			lg.Error("channels.getParticipant failed", zap.Error(err))
			// TODO fatal error?
			return true, nil
		}
	} else {
		switch participant := pcp.Participant.(type) {
		case *tg.ChannelParticipantLeft:
			lg.Warn("Removing update state for channel as user has left")
		case *tg.ChannelParticipantBanned:
			if !participant.Left && !participant.BannedRights.ViewMessages {
				lg.Debug("Membership confirmed (restricted)", zap.Any("participant", participant))
				return true, nil
			}
			lg.Warn("Removing update state for channel as user is left or banned")
		default:
			lg.Debug("Membership confirmed", zap.Any("participant", pcp.Participant))
			return true, nil
		}
	}

	if err := m.cfg.Storage.SetChannelPts(ctx, userID, channelID, -1); err != nil {
		return false, fmt.Errorf("failed to clear pts: %w", err)
	} else if err = m.cfg.OnNotChannelMember(ctx, channelID); err != nil {
		return false, fmt.Errorf("OnNotChannelMember callback failed: %w", err)
	}
	return false, nil
}

// Run notifies manager about user authentication on the telegram server.
//
// If forget is true, local internalState (if exist) will be overwritten
// with remote internalState.
func (m *Manager) Run(ctx context.Context, api API, userID int64, opt AuthOptions) error {
	lg := m.lg.With(
		zap.Int64("user_id", userID),
		zap.Bool("is_bot", opt.IsBot),
		zap.Bool("forget", opt.Forget),
	)
	lg.Debug("Starting update manager")
	defer lg.Debug("Update manager exiting")

	wg, ctx := errgroup.WithContext(ctx)

	if err := func() error {
		m.mux.Lock()
		defer m.mux.Unlock()

		if m.state != nil {
			return errors.Errorf("already authorized (userID: %d)", m.state.selfID)
		}

		state, err := m.loadState(ctx, api, userID, opt.Forget)
		if err != nil {
			return errors.Wrap(err, "load internalState")
		}
		channels := make(map[int64]PtsAccessHashTuple)
		if err := m.cfg.Storage.ForEachChannels(ctx, userID, func(ctx context.Context, channelID int64, pts int) error {
			if pts == -1 {
				return nil
			}
			hash, found, err := m.cfg.AccessHasher.GetChannelAccessHash(ctx, userID, channelID)
			if err != nil {
				return errors.Wrap(err, "get channel access hash")
			} else if !found {
				return nil
			} else if isMember, err := m.checkParticipant(ctx, api, userID, channelID, hash); err != nil {
				return fmt.Errorf("failed to check if user is participant: %w", err)
			} else if !isMember {
				return nil
			}
			channels[channelID] = PtsAccessHashTuple{Pts: pts, AccessHash: hash}
			return nil
		}); err != nil {
			return err
		}

		diffLim := diffLimitUser
		if opt.IsBot {
			diffLim = diffLimitBot
		}

		m.state = newState(ctx, stateConfig{
			State:            state,
			Channels:         channels,
			RawClient:        api,
			Tracer:           m.tracer,
			Logger:           m.cfg.Logger,
			Handler:          m.cfg.Handler,
			OnChannelTooLong: m.cfg.OnChannelTooLong,
			Storage:          m.cfg.Storage,
			Hasher:           m.cfg.AccessHasher,
			SelfID:           userID,
			DiffLimit:        diffLim,
			WorkGroup:        wg,
		})

		return nil
	}(); err != nil {
		return errors.Wrap(err, "setup")
	}
	if opt.OnStart != nil {
		opt.OnStart(ctx)
	}
	wg.Go(func() error {
		return m.state.Run(ctx)
	})
	lg.Debug("Waiting for manager workgroup to exit")
	return wg.Wait()
}

func (m *Manager) RemoveChannel(channelID int64, reason error) {
	if m == nil {
		return
	}
	m.state.RemoveChannel(channelID, reason)
}

func (m *Manager) loadState(ctx context.Context, api API, userID int64, forget bool) (State, error) {
onNotFound:
	var state State
	if forget {
		remote, err := api.UpdatesGetState(ctx)
		if err != nil {
			return State{}, errors.Wrap(err, "get remote internalState")
		}

		state = state.fromRemote(remote)
		if err := m.cfg.Storage.SetState(ctx, userID, state); err != nil {
			return State{}, err
		}

		return state, nil
	}

	state, found, err := m.cfg.Storage.GetState(ctx, userID)
	if err != nil {
		return State{}, errors.Wrap(err, "restore local internalState")
	}

	if !found {
		forget = true
		goto onNotFound
	}

	return state, nil
}

// Reset notifies manager about user logout.
func (m *Manager) Reset() {
	m.mux.Lock()
	defer m.mux.Unlock()
	m.state = nil
}
