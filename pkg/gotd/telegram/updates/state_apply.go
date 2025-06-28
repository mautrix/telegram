package updates

import (
	"context"

	"go.opentelemetry.io/otel/trace"
	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func (s *internalState) applySeq(ctx context.Context, state int, updates []update) error {
	recoverState := false
	for _, u := range updates {
		ptsChanged, err := s.applyCombined(ctx, u.Value.(*tg.UpdatesCombined))
		if err != nil {
			return err
		}

		if ptsChanged {
			recoverState = true
		}
	}

	if err := s.storage.SetSeq(ctx, s.selfID, state); err != nil {
		return err
	}

	if recoverState {
		return s.getDifference(ctx)
	}

	return nil
}

func (s *internalState) applyCombined(ctx context.Context, comb *tg.UpdatesCombined) (ptsChanged bool, err error) {
	ctx, span := s.tracer.Start(ctx, "internalState.applyCombined")
	defer span.End()

	var (
		ents = entities{
			Users: comb.Users,
			Chats: comb.Chats,
		}
	)
	sortUpdatesByPts(comb.Updates)

	for _, u := range comb.Updates {
		switch u := u.(type) {
		case *tg.UpdatePtsChanged:
			ptsChanged = true
			continue
		case *tg.UpdateChannelTooLong:
			st, ok := s.channels[u.ChannelID]
			if !ok {
				s.log.Debug("ChannelTooLong for channel that is not in the internalState, update ignored", zap.Int64("channel_id", u.ChannelID))
				continue
			}
			if err := st.Push(ctx, channelUpdate{
				update:   u,
				entities: ents,
				span:     trace.SpanContextFromContext(ctx),
			}); err != nil {
				return false, err
			}
			continue
		}

		if pts, ptsCount, ok := tg.IsPtsUpdate(u); ok {
			if err := s.handlePts(ctx, pts, ptsCount, u, ents); err != nil {
				return false, err
			}
		}

		if channelID, pts, ptsCount, ok, err := tg.IsChannelPtsUpdate(u); ok {
			if err != nil {
				s.log.Debug("Invalid channel update", zap.Error(err)) //, zap.Any("update", u))
				continue
			}
			if err := s.handleChannel(ctx, channelID, comb.Date, pts, ptsCount, channelUpdate{
				update:   u,
				entities: ents,
				span:     trace.SpanContextFromContext(ctx),
			}); err != nil {
				return false, err
			}
		}

		if qts, ok := tg.IsQtsUpdate(u); ok {
			if err := s.handleQts(ctx, qts, u, ents); err != nil {
				return false, err
			}
		}
	}

	if err := s.handler.Handle(ctx, &tg.Updates{
		Updates: comb.Updates,
		Users:   ents.Users,
		Chats:   ents.Chats,
	}); err != nil {
		return false, err
	}

	setDate, setSeq := comb.Date > s.date, comb.Seq > 0
	switch {
	case setDate && setSeq:
		if err := s.storage.SetDateSeq(ctx, s.selfID, comb.Date, comb.Seq); err != nil {
			return false, err
		}

		s.date = comb.Date
		s.seq.SetState(comb.Seq, "seq update")
	case setDate:
		if err := s.storage.SetDate(ctx, s.selfID, comb.Date); err != nil {
			return false, err
		}
		s.date = comb.Date
	case setSeq:
		if err := s.storage.SetSeq(ctx, s.selfID, comb.Seq); err != nil {
			return false, err
		}
		s.seq.SetState(comb.Seq, "seq update")
	}

	return ptsChanged, nil
}

func (s *internalState) applyPts(ctx context.Context, state int, updates []update) error {
	ctx, span := s.tracer.Start(ctx, "internalState.applyPts")
	defer span.End()

	var (
		converted []tg.UpdateClass
		ents      entities
	)

	for _, update := range updates {
		converted = append(converted, update.Value.(tg.UpdateClass))
		ents.Merge(update.Entities)
	}

	if err := s.handler.Handle(ctx, &tg.Updates{
		Updates: converted,
		Users:   ents.Users,
		Chats:   ents.Chats,
	}); err != nil {
		return err
	}

	if err := s.storage.SetPts(ctx, s.selfID, state); err != nil {
		return err
	}

	return nil
}

func (s *internalState) applyQts(ctx context.Context, state int, updates []update) error {
	ctx, span := s.tracer.Start(ctx, "internalState.applyQts")
	defer span.End()

	var (
		converted []tg.UpdateClass
		ents      entities
	)

	for _, update := range updates {
		converted = append(converted, update.Value.(tg.UpdateClass))
		ents.Merge(update.Entities)
	}

	if err := s.handler.Handle(ctx, &tg.Updates{
		Updates: converted,
		Users:   ents.Users,
		Chats:   ents.Chats,
	}); err != nil {
		return err
	}

	// Don't set qts if it's 0, because it means that we are apllying gaps updates
	if state == 0 {
		return nil
	}

	if err := s.storage.SetQts(ctx, s.selfID, state); err != nil {
		return err
	}

	return nil
}
