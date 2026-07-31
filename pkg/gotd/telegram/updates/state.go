package updates

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/go-faster/errors"
	"go.mau.fi/util/exmaps"
	"go.mau.fi/util/exsync"
	"go.opentelemetry.io/otel/trace"
	"go.uber.org/zap"
	"golang.org/x/sync/errgroup"

	"go.mau.fi/mautrix-telegram/pkg/gotd/exchange"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/auth"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

const (
	idleTimeout    = time.Minute * 15
	fastgapTimeout = time.Millisecond * 500

	diffLimitUser = 100
	diffLimitBot  = 100000
)

type tracedUpdate struct {
	update tg.UpdatesClass
	span   trace.SpanContext
}

type internalState struct {
	// Updates channel.
	externalQueue chan tracedUpdate

	// Updates from channel states
	// during updates.getChannelDifference.
	internalQueue chan tracedUpdate

	// Common internalState.
	pts, qts, seq *sequenceBox
	date          int
	idleTimeout   *time.Timer

	// Channel states.
	channels             map[int64]*channelState
	channelsLock         sync.Mutex
	recentlyLeftChannels *exsync.Set[int64]
	savedCommunities     exmaps.Set[int64]

	// Immutable fields.
	client    API
	log       *zap.Logger
	handler   telegram.UpdateHandler
	onTooLong func(channelID int64) error
	storage   StateStorage
	hasher    AccessHasher
	selfID    int64
	diffLim   int
	wg        *errgroup.Group
	tracer    trace.Tracer
}

type stateConfig struct {
	State            State
	Channels         map[int64]PtsAccessHashTuple
	RawClient        API
	Logger           *zap.Logger
	Tracer           trace.Tracer
	Handler          telegram.UpdateHandler
	OnChannelTooLong func(channelID int64) error
	Storage          StateStorage
	Hasher           AccessHasher
	SelfID           int64
	DiffLimit        int
	WorkGroup        *errgroup.Group
}

func newState(ctx context.Context, cfg stateConfig) *internalState {
	s := &internalState{
		externalQueue: make(chan tracedUpdate, 10),
		internalQueue: make(chan tracedUpdate, 10),

		date:        cfg.State.Date,
		idleTimeout: time.NewTimer(idleTimeout),

		channels:             make(map[int64]*channelState),
		recentlyLeftChannels: exsync.NewSet[int64](),
		savedCommunities:     make(exmaps.Set[int64]),

		client:    cfg.RawClient,
		log:       cfg.Logger,
		handler:   cfg.Handler,
		onTooLong: cfg.OnChannelTooLong,
		storage:   cfg.Storage,
		hasher:    cfg.Hasher,
		selfID:    cfg.SelfID,
		diffLim:   cfg.DiffLimit,
		wg:        cfg.WorkGroup,
		tracer:    cfg.Tracer,
	}
	s.pts = newSequenceBox(sequenceConfig{
		InitialState: cfg.State.Pts,
		Apply:        s.applyPts,
		Logger:       s.log.Named("pts"),
		Tracer:       s.tracer,
	})
	s.qts = newSequenceBox(sequenceConfig{
		InitialState: cfg.State.Qts,
		Apply:        s.applyQts,
		Logger:       s.log.Named("qts"),
		Tracer:       s.tracer,
	})
	s.seq = newSequenceBox(sequenceConfig{
		InitialState: cfg.State.Seq,
		Apply:        s.applySeq,
		Logger:       s.log.Named("seq"),
	})

	for id, info := range cfg.Channels {
		if info.Pts == -1 {
			continue
		}
		s.createAndRunChannelState(ctx, id, info.AccessHash, info.Pts)
	}

	return s
}

func (s *internalState) Push(ctx context.Context, u tg.UpdatesClass) error {
	tu := tracedUpdate{
		update: u,
		span:   trace.SpanContextFromContext(ctx),
	}
	select {
	case s.externalQueue <- tu:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// isFatalError returns true if error is fatal so we should stop updates handler.
func isFatalError(err error) bool {
	// See https://github.com/gotd/td/issues/1458.
	if errors.Is(err, exchange.ErrKeyFingerprintNotFound) {
		return true
	}
	if tgerr.Is(err, "AUTH_KEY_UNREGISTERED", "SESSION_EXPIRED") {
		return true
	}
	if auth.IsUnauthorized(err) {
		return true
	}
	return false
}

func (s *internalState) Run(ctx context.Context) error {
	if s == nil {
		return errors.New("invalid: nil internalState")
	}
	if s.log == nil {
		return errors.New("invalid: nil logger")
	}
	s.log.Info("Starting updates handler")
	defer s.log.Info("Updates handler stopped")
	err := s.getDifferenceLogger(ctx)
	if err != nil {
		return fmt.Errorf("initial getDifference failed: %w", err)
	}

	for {
		select {
		case <-ctx.Done():
			return fmt.Errorf("parent context cancelled: %w", ctx.Err())
		case u := <-s.externalQueue:
			ctx := trace.ContextWithSpanContext(ctx, u.span)
			if err = s.handleUpdates(ctx, u.update); err != nil {
				return fmt.Errorf("handleUpdates for external queue failed: %w", err)
			}
		case u := <-s.internalQueue:
			ctx := trace.ContextWithSpanContext(ctx, u.span)
			if err = s.handleUpdates(ctx, u.update); err != nil {
				return fmt.Errorf("handleUpdates for internal queue failed: %w", err)
			}
		case <-s.pts.gapTimeout.C:
			s.log.Debug("Pts gap timeout")
			err = s.getDifferenceLogger(ctx)
		case <-s.qts.gapTimeout.C:
			s.log.Debug("Qts gap timeout")
			err = s.getDifferenceLogger(ctx)
		case <-s.seq.gapTimeout.C:
			s.log.Debug("Seq gap timeout")
			err = s.getDifferenceLogger(ctx)
		case <-s.idleTimeout.C:
			s.log.Debug("Idle timeout")
			err = s.getDifferenceLogger(ctx)
		}
		if err != nil {
			return fmt.Errorf("fatal error from timeout getDifference: %w", err)
		}
	}
}

func (s *internalState) handleUpdates(ctx context.Context, u tg.UpdatesClass) error {
	ctx, span := s.tracer.Start(ctx, "handleUpdates")
	defer span.End()

	s.resetIdleTimer()

	switch u := u.(type) {
	case *tg.Updates:
		s.saveUserHashes(ctx, u.Users)
		s.saveChannelHashes(ctx, u.Chats)
		return s.handleSeq(ctx, &tg.UpdatesCombined{
			Updates:  u.Updates,
			Users:    u.Users,
			Chats:    u.Chats,
			Date:     u.Date,
			Seq:      u.Seq,
			SeqStart: u.Seq,
		})
	case *tg.UpdatesCombined:
		s.saveUserHashes(ctx, u.Users)
		s.saveChannelHashes(ctx, u.Chats)
		return s.handleSeq(ctx, u)
	case *tg.UpdateShort:
		return s.handleUpdates(ctx, &tg.UpdatesCombined{
			Updates: []tg.UpdateClass{u.Update},
			Date:    u.Date,
		})
	case *tg.UpdateShortMessage:
		updateShort := s.convertShortMessage(u)
		if _, found, err := s.hasher.GetUserAccessHash(ctx, s.selfID, u.UserID); err != nil {
			return err
		} else if !found {
			chats, users, err := s.handleDifference(ctx, u.Date)
			if err != nil {
				return err
			}
			return s.handleUpdates(ctx, &tg.UpdatesCombined{
				Updates: []tg.UpdateClass{updateShort.Update},
				Users:   users,
				Chats:   chats,
				Date:    u.Date,
			})
		} else {
			return s.handleUpdates(ctx, updateShort)
		}
	case *tg.UpdateShortChatMessage:
		updateShort := s.convertShortChatMessage(u)
		if _, found, err := s.hasher.GetUserAccessHash(ctx, s.selfID, u.FromID); err != nil {
			return err
		} else if !found {
			chats, users, err := s.handleDifference(ctx, u.Date)
			if err != nil {
				return err
			}
			return s.handleUpdates(ctx, &tg.UpdatesCombined{
				Updates: []tg.UpdateClass{updateShort.Update},
				Users:   users,
				Chats:   chats,
				Date:    u.Date,
			})
		} else {
			return s.handleUpdates(ctx, updateShort)
		}
	case *tg.UpdateShortSentMessage:
		return s.handleUpdates(ctx, s.convertShortSentMessage(u))
	case *tg.UpdatesTooLong:
		err := s.getDifference(ctx)
		if err != nil {
			return fmt.Errorf("getDifference failed in handleUpdates: %w", err)
		}
		return nil
	default:
		panic(fmt.Sprintf("unexpected update type: %T", u))
	}
}

func (s *internalState) handleSeq(ctx context.Context, u *tg.UpdatesCombined) error {
	ctx, span := s.tracer.Start(ctx, "handleSeq")
	defer span.End()

	if err := validateSeq(u.Seq, u.SeqStart); err != nil {
		s.log.Error("Seq validation failed", zap.Error(err), zap.Any("update", u))
		return nil
	}

	// Special case.
	if u.Seq == 0 {
		ptsChanged, err := s.applyCombined(ctx, u)
		if err != nil {
			return fmt.Errorf("applyCombined failed in handleSeq: %w", err)
		}

		if ptsChanged {
			err = s.getDifference(ctx)
			if err != nil {
				return fmt.Errorf("getDifference after applyCombined failed: %w", err)
			}
			return nil
		}

		return nil
	}

	err := s.seq.Handle(ctx, update{
		Value: u,
		State: u.Seq,
		Count: u.Seq - u.SeqStart + 1,
	})
	if err != nil {
		return fmt.Errorf("handleSeq failed: %w", err)
	}
	return nil
}

func (s *internalState) handlePts(ctx context.Context, pts, ptsCount int, u tg.UpdateClass, ents entities) error {
	if err := validatePts(pts, ptsCount); err != nil {
		s.log.Error("Pts validation failed", zap.Error(err), zap.Any("update", u))
		return nil
	}

	return s.pts.Handle(ctx, update{
		Value:    u,
		State:    pts,
		Count:    ptsCount,
		Entities: ents,
	})
}

func (s *internalState) handleQts(ctx context.Context, qts int, u tg.UpdateClass, ents entities) error {
	if err := validateQts(qts); err != nil {
		s.log.Error("Qts validation failed", zap.Error(err), zap.Any("update", u))
		return nil
	}

	return s.qts.Handle(ctx, update{
		Value:    u,
		State:    qts,
		Count:    1,
		Entities: ents,
	})
}

func (s *internalState) handleChannel(ctx context.Context, channelID int64, date, pts, ptsCount int, cu channelUpdate) error {
	if err := validatePts(pts, ptsCount); err != nil {
		s.log.Error("Pts validation failed", zap.Error(err), zap.Any("update", cu.update))
		return nil
	}
	found := false
	for _, ent := range cu.entities.Chats {
		if ent.GetID() == channelID {
			found = true
			switch te := ent.(type) {
			case *tg.Channel:
				if te.Left {
					s.log.Info("Not adding new channel state for left channel", zap.Int64("channel_id", channelID))
					return nil
				}
				s.recentlyLeftChannels.Remove(channelID)
			case *tg.ChannelForbidden:
				s.log.Info("Not adding new channel state for forbidden channel", zap.Int64("channel_id", channelID))
				return nil
			}
			break
		}
	}
	if !found && s.recentlyLeftChannels.Has(channelID) {
		s.log.Info("Not adding new channel state for recently left channel", zap.Int64("channel_id", channelID))
		return nil
	}

	s.channelsLock.Lock()
	state, ok := s.channels[channelID]
	s.channelsLock.Unlock()
	if !ok {
		accessHash, found, err := s.hasher.GetChannelAccessHash(context.Background(), s.selfID, channelID)
		if err != nil {
			s.log.Error("GetAccessHash error", zap.Error(err))
		}

		if !found {
			if date == 0 {
				// Received update has no date field.
				date = s.date - 30
			} else {
				date-- // 1 sec back
			}

			// Try to get access hash from updates.getDifference.
			accessHash, found = s.restoreChannelAccessHash(ctx, channelID, date)
			if !found {
				s.log.Debug("Failed to recover missing access hash, update ignored",
					zap.Int64("channel_id", channelID),
					// zap.Any("update", cu.update),
				)
				return nil
			}
		}

		localPts, found, err := s.storage.GetChannelPts(ctx, s.selfID, channelID)
		if localPts == -1 {
			found = false
		}
		if err != nil {
			localPts = pts - ptsCount
			s.log.Error("GetChannelPts error", zap.Error(err))
		}

		if !found {
			localPts = pts - ptsCount
			if err := s.storage.SetChannelPts(ctx, s.selfID, channelID, localPts); err != nil {
				s.log.Error("SetChannelPts error", zap.Error(err))
			}
		}

		s.log.Info("Adding new channel state", zap.Int64("channel_id", channelID), zap.Int("local_pts", localPts), zap.Int("remote_pts", pts))
		state = s.createAndRunChannelState(ctx, channelID, accessHash, localPts)
	}

	return state.Push(ctx, cu)
}

func (s *internalState) RemoveChannel(channelID int64, reason error) {
	if s == nil {
		return
	}
	s.channelsLock.Lock()
	state, ok := s.channels[channelID]
	s.channelsLock.Unlock()
	if !ok {
		return
	}
	state.stop(fmt.Errorf("%w: %w", ErrRemoveChannelState, reason))
}

func (s *internalState) createAndRunChannelState(ctx context.Context, channelID, accessHash int64, initialPts int) (state *channelState) {
	state = newChannelState(ctx, channelStateConfig{
		Out:              s.internalQueue,
		InitialPts:       initialPts,
		ChannelID:        channelID,
		AccessHash:       accessHash,
		SelfID:           s.selfID,
		Storage:          s.storage,
		DiffLimit:        s.diffLim,
		RawClient:        s.client,
		Handler:          s.handler,
		OnChannelTooLong: s.onTooLong,
		Logger:           s.log.Named("channel").With(zap.Int64("channel_id", channelID)),
		Tracer:           s.tracer,
	})
	s.channelsLock.Lock()
	s.channels[channelID] = state
	s.channelsLock.Unlock()
	s.wg.Go(func() error {
		err := state.Run(ctx)
		if errors.Is(err, ErrRemoveChannelState) {
			s.channelsLock.Lock()
			delete(s.channels, channelID)
			s.channelsLock.Unlock()
			s.log.Info("Removed channel state due to error", zap.Int64("channel_id", channelID), zap.Error(err))
			return nil
		} else if ctx.Err() == nil {
			s.log.Error("Channel state stopped with unexpected error, new messages may stop arriving",
				zap.Int64("channel_id", channelID), zap.Error(err))
			return nil
		}
		return err
	})
	return state
}

func (s *internalState) getDifferenceWithRetry(ctx context.Context, date int) (diff tg.UpdatesDifferenceClass, err error) {
	for {
		diff, err = s.client.UpdatesGetDifference(ctx, &tg.UpdatesGetDifferenceRequest{
			Pts:  s.pts.State(),
			Qts:  s.qts.State(),
			Date: date,
		})
		if err == nil || isFatalError(err) {
			return diff, err
		}
		dur, ok := tgerr.AsFloodWait(err)
		if ok {
			s.log.Warn("Flood wait error while getting difference", zap.Duration("wait", dur))
		} else {
			s.log.Error("Failed to get difference, retrying in 5 seconds...", zap.Error(err))
			dur = 5 * time.Second
		}
		select {
		case <-time.After(dur):
		case <-ctx.Done():
			return nil, fmt.Errorf("context canceled while waiting to retry: %w", ctx.Err())
		}
	}
}

func (s *internalState) getDifference(ctx context.Context) error {
	ctx, span := s.tracer.Start(ctx, "getDifference")
	defer span.End()

	s.resetIdleTimer()
	s.pts.gaps.Clear()
	s.qts.gaps.Clear()
	s.seq.gaps.Clear()

	s.log.Debug("Getting difference")

	setState := func(state tg.UpdatesState, reason string) {
		if err := s.storage.SetState(ctx, s.selfID, State{}.fromRemote(&state)); err != nil {
			s.log.Warn("SetState error", zap.Error(err))
		}

		s.pts.SetState(state.Pts, reason)
		s.qts.SetState(state.Qts, reason)
		s.seq.SetState(state.Seq, reason)
		s.date = state.Date
	}

	diff, err := s.getDifferenceWithRetry(ctx, s.date)
	if err != nil {
		return errors.Wrap(err, "get difference")
	}

	s.log.Debug("Difference received", zap.String("diff", fmt.Sprintf("%T", diff)))

	switch diff := diff.(type) {
	case *tg.UpdatesDifference:
		if len(diff.OtherUpdates) > 0 {
			if err := s.handleUpdates(ctx, &tg.UpdatesCombined{
				Updates: diff.OtherUpdates,
				Users:   diff.Users,
				Chats:   diff.Chats,
			}); err != nil {
				return errors.Wrap(err, "handle diff.OtherUpdates")
			}
		}

		if len(diff.NewMessages) > 0 || len(diff.NewEncryptedMessages) > 0 {
			if err := s.handler.Handle(ctx, &tg.Updates{
				Updates: append(
					msgsToUpdates(diff.NewMessages, false),
					encryptedMsgsToUpdates(diff.NewEncryptedMessages)...,
				),
				Users: diff.Users,
				Chats: diff.Chats,
			}); err != nil {
				return err
			}
		}

		setState(diff.State, "updates.Difference")
		return nil

	// No events.
	case *tg.UpdatesDifferenceEmpty:
		if err := s.storage.SetDateSeq(ctx, s.selfID, diff.Date, diff.Seq); err != nil {
			s.log.Warn("SetDateSeq error", zap.Error(err))
		}

		s.date = diff.Date
		s.seq.SetState(diff.Seq, "updates.differenceEmpty")
		return nil

	// Incomplete list of occurred events.
	case *tg.UpdatesDifferenceSlice:
		if len(diff.OtherUpdates) > 0 {
			if err := s.handleUpdates(ctx, &tg.UpdatesCombined{
				Updates: diff.OtherUpdates,
				Users:   diff.Users,
				Chats:   diff.Chats,
				Date:    diff.IntermediateState.Date,
			}); err != nil {
				return err
			}
		}

		if len(diff.NewMessages) > 0 || len(diff.NewEncryptedMessages) > 0 {
			if err := s.handler.Handle(ctx, &tg.Updates{
				Updates: append(
					msgsToUpdates(diff.NewMessages, false),
					encryptedMsgsToUpdates(diff.NewEncryptedMessages)...,
				),
				Users: diff.Users,
				Chats: diff.Chats,
			}); err != nil {
				return err
			}
		}

		setState(diff.IntermediateState, "updates.differenceSlice")
		return s.getDifference(ctx)

	// The difference is too long, and the specified internalState must be used to refetch updates.
	case *tg.UpdatesDifferenceTooLong:
		if err := s.storage.SetPts(ctx, s.selfID, diff.Pts); err != nil {
			s.log.Error("SetPts error", zap.Error(err))
		}
		s.pts.SetState(diff.Pts, "updates.differenceTooLong")
		return s.getDifference(ctx)

	default:
		return errors.Errorf("unexpected diff type: %T", diff)
	}
}

func (s *internalState) getDifferenceLogger(ctx context.Context) error {
	if err := s.getDifference(ctx); err != nil {
		if isFatalError(err) {
			return err
		}
		s.log.Error("get difference error", zap.Error(err))
	}
	return nil
}

func (s *internalState) resetIdleTimer() {
	if len(s.idleTimeout.C) > 0 {
		<-s.idleTimeout.C
	}
	_ = s.idleTimeout.Reset(idleTimeout)
}
