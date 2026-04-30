// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Tulir Asokan
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

package connector

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"time"

	"go.mau.fi/util/jsontime"
	"go.mau.fi/util/ptr"
	"go.mau.fi/util/variationselector"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

func (tc *TelegramConnector) GetCapabilities() *bridgev2.NetworkGeneralCapabilities {
	return &bridgev2.NetworkGeneralCapabilities{
		DisappearingMessages: true,
		Provisioning: bridgev2.ProvisioningCapabilities{
			ImagePackImport: true,
			ResolveIdentifier: bridgev2.ResolveIdentifierCapabilities{
				CreateDM:       true,
				LookupPhone:    true,
				LookupUsername: true,
				ContactList:    true,
				Search:         true,
			},
			GroupCreation: map[string]bridgev2.GroupTypeCapabilities{
				"group": {
					TypeDescription: "a normal group",
					Name:            bridgev2.GroupFieldCapability{Allowed: true, Required: true, MaxLength: 255},
					Participants:    bridgev2.GroupFieldCapability{Allowed: true, Required: true, MinLength: 1, MaxLength: 200},
					// TODO implement
					//Disappear:       bridgev2.GroupFieldCapability{Allowed: true},
				},
				// TODO
				//"channel": {},
				//"supergroup": {},
			},
		},
	}
}

func (tc *TelegramConnector) GetBridgeInfoVersion() (info, capabilities int) {
	return 1, 8
}

// TODO get these from getConfig instead of hardcoding?

const MaxTextLength = 4096
const MaxCaptionLength = 1024
const MaxFileSize = 2 * 1024 * 1024 * 1024

var formattingCaps = event.FormattingFeatureMap{
	event.FmtBold:               event.CapLevelFullySupported,
	event.FmtItalic:             event.CapLevelFullySupported,
	event.FmtUnderline:          event.CapLevelFullySupported,
	event.FmtStrikethrough:      event.CapLevelFullySupported,
	event.FmtInlineCode:         event.CapLevelFullySupported,
	event.FmtCodeBlock:          event.CapLevelFullySupported,
	event.FmtSyntaxHighlighting: event.CapLevelFullySupported,
	event.FmtBlockquote:         event.CapLevelFullySupported,
	event.FmtInlineLink:         event.CapLevelFullySupported,
	event.FmtUserLink:           event.CapLevelFullySupported,
	// TODO support room links and event links (convert to appropriate t.me links)
	event.FmtUnorderedList: event.CapLevelPartialSupport,
	event.FmtOrderedList:   event.CapLevelPartialSupport,
	event.FmtListStart:     event.CapLevelPartialSupport,
	event.FmtListJumpValue: event.CapLevelDropped,
	// TODO support custom emojis in messages
	event.FmtCustomEmoji:   event.CapLevelDropped,
	event.FmtSpoiler:       event.CapLevelFullySupported,
	event.FmtSpoilerReason: event.CapLevelDropped,
	event.FmtHeaders:       event.CapLevelPartialSupport,
}

var fileCaps = event.FileFeatureMap{
	event.MsgImage: {
		MimeTypes: map[string]event.CapabilitySupportLevel{
			"image/jpeg": event.CapLevelFullySupported,
			"image/webp": event.CapLevelPartialSupport,
			"image/png":  event.CapLevelPartialSupport,
			"image/gif":  event.CapLevelPartialSupport,
		},
		Caption:          event.CapLevelFullySupported,
		MaxCaptionLength: MaxCaptionLength,
		MaxSize:          10 * 1024 * 1024,
	},
	event.MsgVideo: {
		MimeTypes: map[string]event.CapabilitySupportLevel{
			"video/mp4": event.CapLevelFullySupported,
		},
		Caption:          event.CapLevelFullySupported,
		MaxCaptionLength: MaxCaptionLength,
		MaxSize:          MaxFileSize,
	},
	event.MsgAudio: {
		MimeTypes: map[string]event.CapabilitySupportLevel{
			"audio/mpeg": event.CapLevelFullySupported,
			"audio/mp4":  event.CapLevelFullySupported,
			// TODO some other formats are probably supported too
		},
		Caption:          event.CapLevelFullySupported,
		MaxCaptionLength: MaxCaptionLength,
		MaxSize:          MaxFileSize,
	},
	event.MsgFile: {
		MimeTypes: map[string]event.CapabilitySupportLevel{
			"*/*": event.CapLevelFullySupported,
		},
		Caption:          event.CapLevelFullySupported,
		MaxCaptionLength: MaxCaptionLength,
		MaxSize:          MaxFileSize,
	},
	event.CapMsgGIF: {
		MimeTypes: map[string]event.CapabilitySupportLevel{
			"image/gif": event.CapLevelPartialSupport,
			"video/mp4": event.CapLevelFullySupported,
		},
		Caption:          event.CapLevelFullySupported,
		MaxCaptionLength: MaxCaptionLength,
		MaxSize:          MaxFileSize,
	},
	event.CapMsgSticker: {
		MimeTypes: map[string]event.CapabilitySupportLevel{
			"image/webp": event.CapLevelFullySupported,
			// These are converted to webp
			"image/jpeg": event.CapLevelPartialSupport,
			"image/png":  event.CapLevelPartialSupport,
			// These will only go through if they're from an imported Telegram pack
			"video/lottie+json": event.CapLevelPartialSupport,
			"video/webm":        event.CapLevelPartialSupport,
		},
	},
	event.CapMsgVoice: {
		MimeTypes: map[string]event.CapabilitySupportLevel{
			"audio/ogg":  event.CapLevelFullySupported,
			"audio/mpeg": event.CapLevelFullySupported,
			"audio/mp4":  event.CapLevelFullySupported,
		},
		Caption:          event.CapLevelFullySupported,
		MaxCaptionLength: MaxCaptionLength,
		MaxSize:          MaxFileSize,
	},
}
var premiumFileCaps event.FileFeatureMap

func init() {
	premiumFileCaps = make(event.FileFeatureMap, len(fileCaps))
	for k, v := range fileCaps {
		cloned := ptr.Clone(v)
		if k == event.MsgFile || k == event.MsgVideo || k == event.MsgAudio {
			cloned.MaxSize *= 2
		}
		cloned.MaxCaptionLength *= 2
		premiumFileCaps[k] = cloned
	}
}

func hashEmojiList(emojis []string) string {
	hasher := sha256.New()
	for _, emoji := range emojis {
		hasher.Write([]byte(emoji))
	}
	return hex.EncodeToString(hasher.Sum(nil))[:8]
}

func makeTimerList() []jsontime.Milliseconds {
	const day = 24 * time.Hour
	const week = 7 * day
	const month = 31 * day
	const year = 365 * day
	return []jsontime.Milliseconds{
		jsontime.MS(1 * day),
		jsontime.MS(2 * day),
		jsontime.MS(3 * day),
		jsontime.MS(4 * day),
		jsontime.MS(5 * day),
		jsontime.MS(6 * day),
		jsontime.MS(1 * week),
		jsontime.MS(2 * week),
		jsontime.MS(3 * week),
		jsontime.MS(1 * month),
		jsontime.MS(2 * month),
		jsontime.MS(3 * month),
		jsontime.MS(4 * month),
		jsontime.MS(5 * month),
		jsontime.MS(6 * month),
		jsontime.MS(1 * year),
	}
}

var telegramTimers = makeTimerList()

func (tc *TelegramClient) GetCapabilities(ctx context.Context, portal *bridgev2.Portal) *event.RoomFeatures {
	baseID := "fi.mau.telegram.capabilities.2025_11_24"
	feat := &event.RoomFeatures{
		Formatting:          formattingCaps,
		File:                fileCaps,
		MaxTextLength:       MaxTextLength,
		LocationMessage:     event.CapLevelFullySupported,
		Reply:               event.CapLevelFullySupported,
		Edit:                event.CapLevelFullySupported,
		Delete:              event.CapLevelFullySupported,
		Reaction:            event.CapLevelFullySupported,
		ReactionCount:       1,
		ReadReceipts:        true,
		TypingNotifications: true,

		DisappearingTimer: &event.DisappearingTimerCapability{
			Types:  []event.DisappearingType{event.DisappearingTypeAfterSend},
			Timers: telegramTimers,
		},
		State: event.StateFeatureMap{
			event.StateRoomName.Type:                {Level: event.CapLevelFullySupported},
			event.StateRoomAvatar.Type:              {Level: event.CapLevelFullySupported},
			event.StateBeeperDisappearingTimer.Type: {Level: event.CapLevelFullySupported},
		},
	}
	// TODO non-admins can only edit messages within 48 hours

	reactions := portal.Metadata.(*PortalMetadata).AllowedReactions
	if reactions == nil {
		baseID += "+reactions_any"
		feat.AllowedReactions, feat.CustomEmojiReactions = tc.getAvailableReactionsForCapability(ctx)
		if len(feat.AllowedReactions) > 0 {
			baseID += "+any_list_" + hashEmojiList(feat.AllowedReactions)
		}
	} else if len(reactions) == 0 {
		baseID += "+reactions_none"
		feat.Reaction = event.CapLevelRejected
	} else {
		baseID += "+reactions_" + hashEmojiList(reactions)
		feat.AllowedReactions = reactions
	}
	for i, react := range feat.AllowedReactions {
		feat.AllowedReactions[i] = variationselector.Add(react)
	}
	if tc.isPremiumCache.Load() {
		baseID += "+premium"
		feat.File = premiumFileCaps
		feat.ReactionCount = 3
	}
	portalMetadata := portal.Metadata.(*PortalMetadata)
	peerType, _, topicID, _ := ids.ParsePortalID(portal.ID)
	if topicID > 0 {
		baseID += "+topic"
		// TODO do topics have other changes?
		delete(feat.State, event.StateRoomAvatar.Type)
		delete(feat.State, event.StateBeeperDisappearingTimer.Type)
		feat.DisappearingTimer = nil
	} else if topicID == ids.TopicIDSpaceRoom {
		baseID += "+spaceroom"
		feat = &event.RoomFeatures{}
	}

	switch portal.RoomType {
	case database.RoomTypeDM:
		baseID += "+dm"
		feat.DeleteChat = true
		feat.DeleteChatForEveryone = true
		feat.State = event.StateFeatureMap{
			event.StateBeeperDisappearingTimer.Type: {Level: event.CapLevelFullySupported},
		}
	default:
		// Group creators can delete the chat for everyone, unless it's a large channel
		if peerType == ids.PeerTypeChat || portalMetadata.ParticipantsCount < 1000 || topicID > 0 {
			baseID += "+deletablegroup"
			feat.DeleteChatForEveryone = true
		}
	}

	feat.ID = baseID
	return feat
}
