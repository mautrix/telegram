// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 Tulir Asokan
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.

package connector

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

type portalApprovalInfo struct {
	PeerType string
	EntityID int64
	TopicID  int
	Title    string
	Username string
	IsBot    bool
}

func (tc *TelegramClient) portalApprovalInfoFromPeer(peer tg.PeerClass, topicID int, entities tg.Entities) portalApprovalInfo {
	info := portalApprovalInfo{TopicID: topicID}
	switch typed := peer.(type) {
	case *tg.PeerUser:
		info.PeerType = string(ids.PeerTypeUser)
		info.EntityID = typed.UserID
		if user, ok := entities.Users[typed.UserID]; ok {
			info.Title = strings.TrimSpace(user.FirstName + " " + user.LastName)
			info.Username = user.Username
			info.IsBot = user.Bot
		}
	case *tg.PeerChat:
		info.PeerType = string(ids.PeerTypeChat)
		info.EntityID = typed.ChatID
		if chat, ok := entities.Chats[typed.ChatID]; ok {
			info.Title = chat.Title
		}
	case *tg.PeerChannel:
		info.PeerType = string(ids.PeerTypeChannel)
		info.EntityID = typed.ChannelID
		if channel, ok := entities.Channels[typed.ChannelID]; ok {
			if channel.Megagroup {
				info.PeerType = "supergroup"
			}
			info.Title = channel.Title
			info.Username = channel.Username
		}
	}
	if info.Title == "" {
		info.Title = fmt.Sprintf("%s:%d", info.PeerType, info.EntityID)
	}
	return info
}

func (tc *TelegramClient) portalApprovalInfoFromObject(portalKey networkid.PortalKey, rawChat any) portalApprovalInfo {
	peerType, entityID, topicID, _ := ids.ParsePortalID(portalKey.ID)
	info := portalApprovalInfo{
		PeerType: string(peerType),
		EntityID: entityID,
		TopicID:  topicID,
		Title:    fmt.Sprintf("%s:%d", peerType, entityID),
	}
	switch chat := rawChat.(type) {
	case *tg.User:
		info.Title = strings.TrimSpace(chat.FirstName + " " + chat.LastName)
		info.Username = chat.Username
		info.IsBot = chat.Bot
	case *tg.Chat:
		info.Title = chat.Title
	case *tg.Channel:
		if chat.Megagroup {
			info.PeerType = "supergroup"
		}
		info.Title = chat.Title
		info.Username = chat.Username
	}
	return info
}

func (tc *TelegramClient) portalApprovalInfoFromDialog(portalKey networkid.PortalKey, dialog *tg.Dialog, users map[int64]tg.UserClass, chats map[int64]tg.ChatClass) portalApprovalInfo {
	switch peer := dialog.Peer.(type) {
	case *tg.PeerUser:
		return tc.portalApprovalInfoFromObject(portalKey, users[peer.UserID])
	case *tg.PeerChat:
		return tc.portalApprovalInfoFromObject(portalKey, chats[peer.ChatID])
	case *tg.PeerChannel:
		return tc.portalApprovalInfoFromObject(portalKey, chats[peer.ChannelID])
	default:
		return tc.portalApprovalInfoFromObject(portalKey, nil)
	}
}

func (tc *TelegramClient) portalApprovalAutoAllowed(info portalApprovalInfo) bool {
	cfg := tc.main.Config.PortalApproval.AutoCreate
	switch info.PeerType {
	case string(ids.PeerTypeUser):
		if info.IsBot {
			if cfg.Bots != nil {
				return *cfg.Bots
			}
			return cfg.PrivateChats
		}
		return cfg.PrivateChats
	case string(ids.PeerTypeChat):
		return cfg.Groups
	case "supergroup":
		return cfg.Supergroups
	case string(ids.PeerTypeChannel):
		return cfg.Channels
	default:
		return false
	}
}

func (tc *TelegramClient) portalApprovalStorageKey(portalKey networkid.PortalKey) networkid.PortalKey {
	peerType, entityID, _, err := ids.ParsePortalID(portalKey.ID)
	if err != nil {
		return portalKey
	}
	return tc.makePortalKeyFromID(peerType, entityID, 0)
}

func (tc *TelegramClient) ensurePortalApproved(ctx context.Context, portalKey networkid.PortalKey, info portalApprovalInfo, lastEvent string) (bool, error) {
	if !tc.main.Config.PortalApproval.Enabled {
		return true, nil
	}
	if err := tc.cleanupOldPendingPortalApprovals(ctx); err != nil {
		return false, err
	}
	approvalKey := tc.portalApprovalStorageKey(portalKey)
	userID := tc.telegramUserID
	item, err := tc.main.Store.Approval.GetByPortal(ctx, userID, approvalKey.ID, approvalKey.Receiver)
	if err != nil {
		return false, err
	}
	if item != nil && item.Status == store.PortalApprovalAllowed {
		return true, nil
	} else if item != nil {
		return false, nil
	}
	portal, err := tc.main.Bridge.GetExistingPortalByKey(ctx, portalKey)
	if err != nil {
		return false, err
	} else if portal != nil && portal.MXID != "" {
		return true, nil
	}

	status := store.PortalApprovalPending
	overwriteStatus := false
	if tc.portalApprovalAutoAllowed(info) {
		status = store.PortalApprovalAllowed
		overwriteStatus = true
	} else if !tc.main.Config.PortalApproval.Pending.Enabled {
		return false, nil
	}

	_, err = tc.main.Store.Approval.Upsert(ctx, store.PortalApproval{
		UserID:         userID,
		PortalID:       approvalKey.ID,
		PortalReceiver: approvalKey.Receiver,
		PeerType:       info.PeerType,
		EntityID:       info.EntityID,
		TopicID:        0,
		Title:          info.Title,
		Username:       info.Username,
		Status:         status,
		LastEvent:      lastEvent,
	}, overwriteStatus)
	if err != nil {
		return false, err
	}
	zerolog.Ctx(ctx).Info().
		Stringer("portal_key", portalKey).
		Str("title", info.Title).
		Str("approval_status", string(status)).
		Msg("Stored Telegram portal approval state")
	return status == store.PortalApprovalAllowed, nil
}

func (tc *TelegramClient) ensurePortalApprovedForPeer(ctx context.Context, portalKey networkid.PortalKey, peer tg.PeerClass, topicID int, entities tg.Entities, lastEvent string) (bool, error) {
	return tc.ensurePortalApproved(ctx, portalKey, tc.portalApprovalInfoFromPeer(peer, topicID, entities), lastEvent)
}

func (tc *TelegramClient) ensurePortalApprovedForObject(ctx context.Context, portalKey networkid.PortalKey, chat any, lastEvent string) (bool, error) {
	return tc.ensurePortalApproved(ctx, portalKey, tc.portalApprovalInfoFromObject(portalKey, chat), lastEvent)
}

func (tc *TelegramClient) cleanupOldPendingPortalApprovals(ctx context.Context) error {
	maxAgeHours := tc.main.Config.PortalApproval.Pending.MaxAgeHours
	if maxAgeHours <= 0 {
		return nil
	}
	cutoff := time.Now().Add(-time.Duration(maxAgeHours) * time.Hour).Unix()
	deleted, err := tc.main.Store.Approval.DeletePendingOlderThan(ctx, tc.telegramUserID, cutoff)
	if err != nil {
		return err
	} else if deleted > 0 {
		zerolog.Ctx(ctx).Info().
			Int("max_age_hours", maxAgeHours).
			Int64("deleted", deleted).
			Msg("Cleaned old pending Telegram portal approval entries")
	}
	return nil
}
