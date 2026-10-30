// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 Automattic Inc.
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
	"encoding/base64"
	"fmt"
	"strconv"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/matrixfmt"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

const pollVotePartPrefix = "vote:"

var (
	_ bridgev2.PollHandlingNetworkAPI        = (*TelegramClient)(nil)
	_ bridgev2.PollResultsFetchingNetworkAPI = (*TelegramClient)(nil)
)

func telegramToMatrixAnswerID(meta *PollMetadata, option []byte) string {
	answerID := base64.RawURLEncoding.EncodeToString(option)
	if meta != nil {
		if mxID, ok := meta.MatrixAnswerIDs[answerID]; ok {
			return mxID
		}
	}
	return answerID
}

func matrixToTelegramPollOption(meta *PollMetadata, answerID string) ([]byte, error) {
	for option, mxID := range meta.MatrixAnswerIDs {
		if mxID == answerID {
			return base64.RawURLEncoding.DecodeString(option)
		}
	}
	return base64.RawURLEncoding.DecodeString(answerID)
}

func (tc *TelegramClient) FetchPollResults(ctx context.Context, portal *bridgev2.Portal, msg *database.Message) (*bridgev2.PollResults, error) {
	pollMeta := msg.Metadata.(*MessageMetadata).Poll
	if pollMeta == nil {
		return nil, mautrix.MNotFound.WithMessage("Message is not a poll")
	}
	_, tgMsgID, err := ids.ParseMessageID(msg.ID)
	if err != nil {
		return nil, err
	}
	peer, _, err := tc.inputPeerForPortalID(ctx, portal.ID)
	if err != nil {
		return nil, err
	}
	updates, err := APICallWithUpdates(ctx, tc, func() (*tg.Updates, error) {
		u, err := tc.client.API().MessagesGetPollResults(ctx, &tg.MessagesGetPollResultsRequest{
			Peer:  peer,
			MsgID: tgMsgID,
		})
		if err != nil {
			return nil, err
		} else if updates, ok := u.(*tg.Updates); ok {
			return updates, nil
		} else {
			return nil, fmt.Errorf("unexpected updates type %T", u)
		}
	})
	if err != nil {
		return nil, fmt.Errorf("failed to get poll results: %w", err)
	}
	for _, update := range updates.Updates {
		if pollUpdate, ok := update.(*tg.UpdateMessagePoll); ok {
			return tc.convertPollResults(pollMeta, pollUpdate), nil
		}
	}
	return nil, fmt.Errorf("no poll results in response")
}

func (tc *TelegramClient) convertPollResults(meta *PollMetadata, update *tg.UpdateMessagePoll) *bridgev2.PollResults {
	answerResults := make(map[string]tg.PollAnswerVoters, len(update.Results.Results))
	for _, answer := range update.Results.Results {
		answerResults[string(answer.Option)] = answer
	}
	var options [][]byte
	poll, hasPoll := update.GetPoll()
	if hasPoll {
		for _, answer := range poll.Answers {
			if typedAnswer, ok := answer.(*tg.PollAnswer); ok {
				options = append(options, typedAnswer.Option)
			}
		}
	} else {
		for _, answer := range update.Results.Results {
			options = append(options, answer.Option)
		}
	}
	results := &bridgev2.PollResults{
		Closed:      hasPoll && poll.Closed,
		TotalVoters: update.Results.TotalVoters,
		Answers:     make([]*bridgev2.PollAnswerResult, len(options)),
	}
	for i, option := range options {
		answer := answerResults[string(option)]
		results.Answers[i] = &bridgev2.PollAnswerResult{
			ID:           telegramToMatrixAnswerID(meta, option),
			Voters:       answer.Voters,
			Chosen:       answer.Chosen && !update.Results.Min,
			RecentVoters: make([]networkid.UserID, len(answer.RecentVoters)),
		}
		for j, voter := range answer.RecentVoters {
			results.Answers[i].RecentVoters[j] = tc.getPeerSender(voter).Sender
		}
	}
	return results
}

func (tc *TelegramClient) HandleMatrixPollStart(ctx context.Context, msg *bridgev2.MatrixPollStart) (*bridgev2.MatrixMessageResponse, error) {
	if msg.Portal.RoomType == database.RoomTypeSpace {
		return nil, errSpacePortal
	}
	err := tc.clientInitialized.Wait(ctx)
	if err != nil {
		return nil, err
	}
	peer, topicID, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return nil, err
	}

	answers := make([]tg.PollAnswerClass, len(msg.Content.PollStart.Answers))
	for i, answer := range msg.Content.PollStart.Answers {
		answers[i] = &tg.PollAnswer{
			Text:   tc.matrixToTelegramText(ctx, msg.Portal, &answer.MSC1767Message),
			Option: []byte(strconv.Itoa(i)),
		}
	}
	randomID := parseRandomID(msg.InputTransactionID)
	updates, err := tc.client.API().MessagesSendMedia(ctx, &tg.MessagesSendMediaRequest{
		Peer: peer,
		Media: &tg.InputMediaPoll{
			Poll: tg.Poll{
				PublicVoters:   !isBroadcastPortal(msg.Portal),
				MultipleChoice: msg.Content.PollStart.MaxSelections != 1,
				Question:       tc.matrixToTelegramText(ctx, msg.Portal, &msg.Content.PollStart.Question),
				Answers:        answers,
			},
		},
		ReplyTo:  makeInputReplyTo(ctx, msg.ReplyTo, topicID),
		RandomID: randomID,
	})
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Msg("failed to send poll to Telegram")
		return nil, tc.humaniseSendError(err)
	}
	resp, sentMessage, err := tc.makeSendResponse(ctx, msg.Portal, updates, randomID, "", "")
	if err != nil {
		return nil, err
	}
	if sentMedia, ok := sentMessage.GetMedia(); ok {
		if sentPoll, ok := sentMedia.(*tg.MessageMediaPoll); ok && len(sentPoll.Poll.Answers) == len(answers) {
			answers = sentPoll.Poll.Answers
		}
	}
	pollMeta := &PollMetadata{MatrixAnswerIDs: make(map[string]string, len(answers))}
	for i, answer := range answers {
		if typedAnswer, ok := answer.(*tg.PollAnswer); ok {
			pollMeta.MatrixAnswerIDs[telegramToMatrixAnswerID(nil, typedAnswer.Option)] = msg.Content.PollStart.Answers[i].ID
		}
	}
	resp.DB.Metadata.(*MessageMetadata).Poll = pollMeta
	return resp, nil
}

func (tc *TelegramClient) HandleMatrixPollVote(ctx context.Context, msg *bridgev2.MatrixPollVote) (*bridgev2.MatrixMessageResponse, error) {
	pollMeta := msg.VoteTo.Metadata.(*MessageMetadata).Poll
	if pollMeta == nil {
		return nil, fmt.Errorf("%w: votes in this poll can't be bridged", bridgev2.ErrUnsupportedMessageType)
	}
	err := tc.clientInitialized.Wait(ctx)
	if err != nil {
		return nil, err
	}
	_, tgMsgID, err := ids.ParseMessageID(msg.VoteTo.ID)
	if err != nil {
		return nil, err
	}
	peer, _, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return nil, err
	}
	options := make([][]byte, len(msg.Content.Response.Answers))
	for i, answer := range msg.Content.Response.Answers {
		options[i], err = matrixToTelegramPollOption(pollMeta, answer)
		if err != nil {
			return nil, fmt.Errorf("unknown poll answer ID %q: %w", answer, err)
		}
	}
	_, err = tc.client.API().MessagesSendVote(ctx, &tg.MessagesSendVoteRequest{
		Peer:    peer,
		MsgID:   tgMsgID,
		Options: options,
	})
	if err != nil {
		return nil, tc.humaniseSendError(err)
	}

	return &bridgev2.MatrixMessageResponse{
		DB: &database.Message{
			ID:        msg.VoteTo.ID,
			PartID:    networkid.PartID(pollVotePartPrefix + msg.Event.ID),
			SenderID:  tc.userID,
			Timestamp: msg.VoteTo.Timestamp,
			Metadata:  &MessageMetadata{},
		},
	}, nil
}

func isBroadcastPortal(portal *bridgev2.Portal) bool {
	peerType, _, _, err := ids.ParsePortalID(portal.ID)
	return err == nil && peerType == ids.PeerTypeChannel && !portal.Metadata.(*PortalMetadata).IsSuperGroup
}

func (tc *TelegramClient) matrixToTelegramText(ctx context.Context, portal *bridgev2.Portal, text *event.MSC1767Message) tg.TextWithEntities {
	content := &event.MessageEventContent{MsgType: event.MsgText, Body: text.GetText()}
	if html := text.GetHTML(); html != "" {
		content.Format = event.FormatHTML
		content.FormattedBody = html
	}
	message, entities := matrixfmt.Parse(ctx, tc.matrixParser, content, portal, tc.getMaxMessageLength(ctx, false))
	return tg.TextWithEntities{Text: message, Entities: entities}
}

func (tc *TelegramClient) isBotUser(ctx context.Context, userID int64) bool {
	ghost, err := tc.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID))
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Int64("user_id", userID).Msg("Failed to get ghost to check if user is a bot")
		return false
	}
	return ghost.IsBot
}
