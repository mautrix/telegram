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
	"sync"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/util/jsontime"
	"maunium.net/go/mautrix"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

const telegramViewOnceTTL = 2147483647

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
		return int(limit.Time.Duration / time.Second), nil
	}
	return 0, bridgev2.ErrUnsupportedViewLimitedType
}

func (tc *TelegramClient) getViewLimitedMessageTTL(msg *bridgev2.MatrixMessage) (int, error) {
	limit := msg.Content.BeeperViewLimited
	ttl, err := telegramMediaTTL(limit)
	if err != nil {
		return 0, err
	}
	peerType, peerID, _, err := ids.ParsePortalID(msg.Portal.ID)
	if err != nil {
		return 0, err
	}
	if tc.metadata.IsBot || peerType != ids.PeerTypeUser || peerID == tc.telegramUserID || (ttl == telegramViewOnceTTL && tc.main.Config.DisableViewOnce) || msg.Event.Type == event.EventSticker {
		return 0, bridgev2.ErrUnsupportedViewLimitedType
	}
	switch msg.Content.MsgType {
	case event.MsgImage:
		forceDocument, _ := msg.Event.Content.Raw["fi.mau.telegram.force_document"].(bool)
		mime := msg.Content.GetInfo().MimeType
		if forceDocument || (mime != "image/jpeg" && mime != "image/png" && mime != "image/webp") {
			return 0, bridgev2.ErrUnsupportedViewLimitedType
		}
	case event.MsgVideo:
		if msg.Content.GetInfo().MauGIF {
			return 0, bridgev2.ErrUnsupportedViewLimitedType
		}
	case event.MsgAudio:
		if msg.Content.MSC3245Voice == nil {
			return 0, bridgev2.ErrUnsupportedViewLimitedType
		}
	default:
		return 0, bridgev2.ErrUnsupportedViewLimitedType
	}
	return ttl, nil
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
	if meta.ViewLimited.Type == "count" && tc.main.Config.DisableViewOnce {
		return mautrix.MForbidden.WithMessage("View-once media is disabled")
	}
	// Telegram only accepts content-read receipts for incoming messages.
	if msg.Message.SenderID == tc.userID {
		return nil
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
		tc.rememberViewLimitedMediaRead(messageID, viewedAt)
		err = errors.Join(err, tc.applyViewLimitedMediaRead(ctx, nil, messageID))
	}
	return err
}

type pendingViewLimitedRead struct {
	viewedAt time.Time
	timer    *time.Timer
	retry    *time.Timer
	active   int
}

func (tc *TelegramClient) rememberViewLimitedMediaRead(messageID networkid.MessageID, viewedAt time.Time) {
	tc.viewLimitedReadsLock.Lock()
	defer tc.viewLimitedReadsLock.Unlock()
	pending := tc.getPendingViewLimitedRead(messageID)
	if pending.viewedAt.IsZero() || viewedAt.Before(pending.viewedAt) {
		pending.viewedAt = viewedAt
	}
}

// getPendingViewLimitedRead must be called with viewLimitedReadsLock held.
func (tc *TelegramClient) getPendingViewLimitedRead(messageID networkid.MessageID) *pendingViewLimitedRead {
	if pending := tc.viewLimitedReads[messageID]; pending != nil {
		return pending
	}
	if tc.viewLimitedReads == nil {
		tc.viewLimitedReads = make(map[networkid.MessageID]*pendingViewLimitedRead)
	}
	pending := &pendingViewLimitedRead{}
	tc.viewLimitedReads[messageID] = pending
	// Difference updates may report reads before new messages; uploads may also still be running.
	// Limit retention for reads whose messages will never be bridged.
	pending.timer = time.AfterFunc(30*time.Minute, func() {
		tc.viewLimitedReadsLock.Lock()
		defer tc.viewLimitedReadsLock.Unlock()
		if tc.viewLimitedReads[messageID] == pending {
			if pending.active > 0 {
				return
			}
			if pending.retry != nil {
				pending.retry.Stop()
			}
			delete(tc.viewLimitedReads, messageID)
		}
	})
	return pending
}

func (tc *TelegramClient) pinViewLimitedMediaRead(ctx context.Context, messageID networkid.MessageID) func() {
	tc.viewLimitedReadsLock.Lock()
	pending := tc.getPendingViewLimitedRead(messageID)
	pending.active++
	tc.viewLimitedReadsLock.Unlock()
	release := sync.OnceFunc(func() {
		tc.viewLimitedReadsLock.Lock()
		defer tc.viewLimitedReadsLock.Unlock()
		pending.active--
		if tc.viewLimitedReads[messageID] == pending && pending.active == 0 {
			if pending.viewedAt.IsZero() {
				pending.timer.Stop()
				delete(tc.viewLimitedReads, messageID)
			} else {
				pending.timer.Reset(30 * time.Minute)
			}
		}
	})
	stop := context.AfterFunc(ctx, release)
	return func() { stop(); release() }
}

func (tc *TelegramClient) applyViewLimitedMediaRead(ctx context.Context, portal *bridgev2.Portal, messageID networkid.MessageID) (err error) {
	tc.viewLimitedReadsLock.Lock()
	defer tc.viewLimitedReadsLock.Unlock()
	pending := tc.viewLimitedReads[messageID]
	if pending == nil || pending.viewedAt.IsZero() {
		return nil
	}
	defer func() {
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Msg("Failed to queue viewed media expiry")
			if pending.retry == nil {
				pending.retry = time.AfterFunc(5*time.Second, func() {
					tc.viewLimitedReadsLock.Lock()
					if tc.viewLimitedReads[messageID] != pending {
						tc.viewLimitedReadsLock.Unlock()
						return
					}
					pending.retry = nil
					tc.viewLimitedReadsLock.Unlock()
					tc.applyViewLimitedMediaRead(tc.main.Bridge.BackgroundCtx, nil, messageID)
				})
			}
		}
	}()
	msg, err := tc.main.Bridge.DB.Message.GetFirstPartByID(ctx, tc.loginID, messageID)
	if err != nil || msg == nil {
		return err
	}
	if meta := msg.Metadata.(*MessageMetadata); meta.ViewLimited != nil {
		if portal == nil {
			portal, err = tc.main.Bridge.GetExistingPortalByKey(ctx, msg.Room)
			if err != nil || portal == nil {
				return err
			}
		}
		if portal.MXID == "" {
			return nil
		}
		err = tc.main.Bridge.DisappearLoop.Add(ctx, &database.DisappearingMessage{
			RoomID: portal.MXID, EventID: msg.MXID, Timestamp: msg.Timestamp,
			DisappearingSetting: database.DisappearingSetting{
				Type: "view_limited", DisappearAt: pending.viewedAt.Add(meta.ViewLimited.Time.Duration),
			},
		})
		if err != nil {
			return err
		}
	}
	pending.timer.Stop()
	if pending.retry != nil {
		pending.retry.Stop()
	}
	delete(tc.viewLimitedReads, messageID)
	return nil
}
