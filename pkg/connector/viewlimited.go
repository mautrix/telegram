// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 Killian Lelong
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.

package connector

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/util/jsontime"
	"maunium.net/go/mautrix"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/simplevent"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

const telegramViewOnceTTL = 0x7FFFFFFF

var _ bridgev2.ViewLimitedMediaHandlingNetworkAPI = (*TelegramClient)(nil)

func telegramViewLimit(ttl int) *event.BeeperViewLimitedMedia {
	if ttl == telegramViewOnceTTL {
		return &event.BeeperViewLimitedMedia{Type: "count", Count: 1}
	} else if ttl > 0 {
		return &event.BeeperViewLimitedMedia{Type: "time", Time: jsontime.MS(time.Duration(ttl) * time.Second)}
	}
	return nil
}

func telegramMediaTTL(limit *event.BeeperViewLimitedMedia) (int, error) {
	if limit == nil {
		return 0, nil
	}
	if limit.Type == "count" && limit.Count == 1 && limit.Time.IsZero() {
		return telegramViewOnceTTL, nil
	}
	if limit.Type == "time" && limit.Count == 0 && limit.Time.Duration >= time.Second && limit.Time.Duration <= time.Minute && limit.Time.Duration%time.Second == 0 {
		return int(limit.Time.Duration.Seconds()), nil
	}
	return 0, bridgev2.ErrUnsupportedViewLimitedType
}

func (tc *TelegramClient) HandleMatrixViewLimitedMedia(ctx context.Context, msg *bridgev2.MatrixViewLimitedMedia) error {
	meta, ok := msg.Message.Metadata.(*MessageMetadata)
	if !ok || meta.ViewLimited == nil || msg.Content == nil || *meta.ViewLimited != *msg.Content {
		return mautrix.MInvalidParam.WithMessage("View limit does not match the message")
	}
	channelID, messageID, err := ids.ParseMessageID(msg.Message.ID)
	if err != nil || channelID != 0 || messageID <= 0 || tc.metadata.IsBot {
		return mautrix.MInvalidParam.WithMessage("Invalid view-limited media message")
	}
	if msg.Message.SenderID == tc.userID {
		return mautrix.MInvalidParam.WithMessage("Outgoing media is not view-limited")
	}
	if err = tc.clientInitialized.Wait(ctx); err != nil {
		return err
	}
	_, err = tc.client.API().MessagesReadMessageContents(ctx, []int{messageID})
	if err != nil {
		return fmt.Errorf("failed to mark view-limited media as viewed: %w", err)
	}
	return nil
}

func (tc *TelegramClient) onViewLimitedMediaRead(ctx context.Context, update *tg.UpdateReadMessagesContents) (err error) {
	viewedAt := time.Now()
	if date, ok := update.GetDate(); ok && date > 0 {
		viewedAt = time.Unix(int64(date), 0)
	}
	for _, msgID := range update.Messages {
		messageID := ids.MakeMessageID(int64(0), msgID)
		portalKey, ok := tc.recentMessageRooms.Get(messageID)
		if !ok {
			msg, dbErr := tc.main.Bridge.DB.Message.GetFirstPartByID(ctx, tc.loginID, messageID)
			if dbErr != nil {
				err = errors.Join(err, dbErr)
				continue
			} else if msg == nil {
				continue
			}
			portalKey = msg.Room
		}
		res := tc.main.Bridge.QueueRemoteEvent(tc.userLogin, &simplevent.EventMeta{
			Type:      bridgev2.RemoteEventUnknown,
			PortalKey: portalKey,
			PreHandleFunc: func(ctx context.Context, portal *bridgev2.Portal) {
				msg, dbErr := tc.main.Bridge.DB.Message.GetFirstPartByID(ctx, tc.loginID, messageID)
				if dbErr != nil {
					zerolog.Ctx(ctx).Err(dbErr).Msg("Failed to get viewed media")
					return
				} else if msg == nil || msg.SenderID == tc.userID {
					return
				}
				limit := msg.Metadata.(*MessageMetadata).ViewLimited
				if limit == nil {
					return
				}
				if addErr := tc.main.Bridge.DisappearLoop.Add(ctx, &database.DisappearingMessage{
					RoomID: portal.MXID, EventID: msg.MXID, Timestamp: msg.Timestamp,
					DisappearingSetting: database.DisappearingSetting{
						Type: "view_limited", DisappearAt: viewedAt.Add(limit.Time.Duration),
					},
				}); addErr != nil {
					zerolog.Ctx(ctx).Err(addErr).Msg("Failed to queue viewed media expiry")
				}
			},
		})
		err = errors.Join(err, resultToError(res))
	}
	return err
}
