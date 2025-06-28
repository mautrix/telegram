package updates

import (
	"fmt"

	"go.uber.org/zap"
	"golang.org/x/net/context"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func (s *internalState) saveChannelHashes(ctx context.Context, chats []tg.ChatClass) {
	ctx, span := s.tracer.Start(ctx, "updates.saveChannelHashes")
	defer span.End()

	for _, c := range chats {
		switch c := c.(type) {
		case *tg.Channel:
			if c.Min {
				continue
			}

			if hash, ok := c.GetAccessHash(); ok {
				if _, ok = s.channels[c.ID]; ok {
					continue
				}
				s.log.Debug("New channel access hash",
					zap.Int64("channel_id", c.ID),
					zap.String("title", c.Title),
				)
				if err := s.hasher.SetChannelAccessHash(ctx, s.selfID, c.ID, hash); err != nil {
					s.log.Error("SetChannelAccessHash error", zap.Error(err))
				}
			}
		case *tg.ChannelForbidden:
			if _, ok := s.channels[c.ID]; ok {
				continue
			}
			s.log.Debug("New channel access hash",
				zap.Int64("channel_id", c.ID),
				zap.String("title", c.Title),
			)
			if err := s.hasher.SetChannelAccessHash(ctx, s.selfID, c.ID, c.AccessHash); err != nil {
				s.log.Error("SetChannelAccessHash error", zap.Error(err))
			}
		}
	}
}

func (s *internalState) saveUserHashes(ctx context.Context, chats []tg.UserClass) {
	ctx, span := s.tracer.Start(ctx, "updates.saveChannelHashes")
	defer span.End()

	for _, u := range chats {
		if user, ok := u.(*tg.User); !ok {
			continue
		} else if hash, ok := user.GetAccessHash(); !ok {
			continue
		} else if user.Min {
			s.log.Debug("User is min, not saving access hash")
			continue
		} else {
			s.log.Debug("New user access hash", zap.Int64("user_id", user.ID))
			if err := s.hasher.SetUserAccessHash(ctx, s.selfID, user.ID, hash); err != nil {
				s.log.Error("SetUserAccessHash error", zap.Error(err))
			}
		}
	}
}

func (s *internalState) handleDifference(ctx context.Context, date int) (chats []tg.ChatClass, users []tg.UserClass, err error) {
	ctx, span := s.tracer.Start(ctx, "updates.handleDifference")
	defer span.End()

	diff, err := s.client.UpdatesGetDifference(ctx, &tg.UpdatesGetDifferenceRequest{
		Pts:  s.pts.State(),
		Qts:  s.qts.State(),
		Date: date,
	})
	if err != nil {
		s.log.Error("UpdatesGetDifference error", zap.Error(err))
		return nil, nil, fmt.Errorf("get difference: %w", err)
	}

	switch diff := diff.(type) {
	case *tg.UpdatesDifference:
		chats = diff.Chats
		users = diff.Users
	case *tg.UpdatesDifferenceSlice:
		chats = diff.Chats
		users = diff.Users
	}

	s.saveChannelHashes(ctx, chats)
	s.saveUserHashes(ctx, users)
	return chats, users, nil
}

func (s *internalState) restoreChannelAccessHash(ctx context.Context, channelID int64, date int) (accessHash int64, ok bool) {
	ctx, span := s.tracer.Start(ctx, "updates.restoreAccessHash")
	defer span.End()

	chats, _, err := s.handleDifference(ctx, date)
	if err != nil {
		s.log.Error("getDifference error", zap.Error(err))
		return 0, false
	}

	for _, c := range chats {
		switch c := c.(type) {
		case *tg.Channel:
			if c.Min {
				continue
			}

			if c.ID != channelID {
				continue
			}

			if hash, ok := c.GetAccessHash(); ok {
				return hash, true
			}

		case *tg.ChannelForbidden:
			if c.ID != channelID {
				continue
			}

			return c.AccessHash, true
		}
	}

	return 0, false
}
