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
	"slices"
	"strconv"
	"time"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

const pollVotePartPrefix = "vote:"

var _ bridgev2.PollHandlingNetworkAPI = (*TelegramClient)(nil)

type pollVote struct {
	Sender    bridgev2.EventSender
	Options   [][]byte
	Timestamp time.Time
}

func pollAnswerID(meta *PollMetadata, option []byte) string {
	answerID := base64.RawURLEncoding.EncodeToString(option)
	if meta != nil {
		if mxID, ok := meta.MatrixAnswerIDs[answerID]; ok {
			return mxID
		}
	}
	return answerID
}

func pollOption(meta *PollMetadata, answerID string) ([]byte, error) {
	for option, mxID := range meta.MatrixAnswerIDs {
		if mxID == answerID {
			return base64.RawURLEncoding.DecodeString(option)
		}
	}
	return base64.RawURLEncoding.DecodeString(answerID)
}

func (tc *TelegramClient) canBridgePollVotes(msg *tg.Message, poll *tg.Poll) bool {
	_, isForwarded := msg.GetFwdFrom()
	return tc.main.Config.ExtEvPolls && !tc.metadata.IsBot && poll.PublicVoters && !poll.Closed && !isForwarded
}

func (tc *TelegramClient) pollVoteSender(userID networkid.UserID) bridgev2.EventSender {
	if peerType, id, err := ids.ParseUserID(userID); err == nil && peerType == ids.PeerTypeUser {
		return tc.senderForUserID(id)
	}
	return bridgev2.EventSender{Sender: userID}
}

func (tc *TelegramClient) onMessagePoll(ctx context.Context, update *tg.UpdateMessagePoll) error {
	peer, ok := update.GetPeer()
	if !ok {
		return nil
	}
	msgID, _ := update.GetMsgID()
	return tc.handlePollResults(ctx, ids.MakeMessageID(peer, msgID), update)
}

func (tc *TelegramClient) handlePollResults(ctx context.Context, messageID networkid.MessageID, update *tg.UpdateMessagePoll) error {
	ctx = zerolog.Ctx(ctx).With().
		Str("handler", "poll_results").
		Str("message_id", string(messageID)).
		Int64("poll_id", update.PollID).
		Logger().WithContext(ctx)
	parts, err := tc.main.Bridge.DB.Message.GetAllPartsByID(ctx, tc.loginID, messageID)
	if err != nil {
		return err
	}
	pollIdx := slices.IndexFunc(parts, func(part *database.Message) bool {
		return part.Metadata.(*MessageMetadata).Poll != nil
	})
	if pollIdx < 0 {
		return nil
	}
	portalKey := parts[pollIdx].Room
	_, msgID, err := ids.ParseMessageID(messageID)
	if err != nil {
		return err
	}

	votes, complete := tc.getPollVotes(ctx, portalKey, msgID, &update.Results)
	poll, hasPoll := update.GetPoll()
	closed := hasPoll && poll.Closed
	return resultToError(tc.main.Bridge.QueueRemoteEvent(tc.userLogin, &simplevent.Message[[]pollVote]{
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventMessageUpsert,
			PortalKey: portalKey,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Int("message_id", msgID).Str("tg_event", "updateMessagePoll")
			},
		},
		ID:   messageID,
		Data: votes,
		ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data []pollVote) (*bridgev2.ConvertedMessage, error) {
			return nil, fmt.Errorf("%w: poll message not found", bridgev2.ErrIgnoringRemoteEvent)
		},
		HandleExistingFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, existing []*database.Message, data []pollVote) (bridgev2.UpsertResult, error) {
			return tc.syncPollVotes(ctx, portal, existing, data, complete, closed)
		},
	}))
}

func (tc *TelegramClient) maybeRefreshPolls(ctx context.Context, portal *bridgev2.Portal) error {
	if tc.metadata.IsBot || portal == nil || portal.RoomType == database.RoomTypeSpace {
		return nil
	}
	tc.prevPollRefreshLock.Lock()
	prev, ok := tc.prevPollRefresh[portal.PortalKey]
	if !ok || time.Since(prev) > 20*time.Second {
		ok = false
		tc.prevPollRefresh[portal.PortalKey] = time.Now()
	}
	tc.prevPollRefreshLock.Unlock()
	if ok {
		return nil
	}

	messages, err := tc.main.Bridge.DB.Message.GetLastNInPortal(ctx, portal.PortalKey, 20)
	if err != nil {
		return err
	}
	var inputPeer tg.InputPeerClass
	refreshed := make(map[networkid.MessageID]struct{})
	for _, msg := range messages {
		meta := msg.Metadata.(*MessageMetadata).Poll
		if _, ok := refreshed[msg.ID]; ok || meta == nil || meta.Ended {
			continue
		}
		refreshed[msg.ID] = struct{}{}
		_, msgID, err := ids.ParseMessageID(msg.ID)
		if err != nil {
			continue
		}
		if inputPeer == nil {
			inputPeer, _, err = tc.inputPeerForPortalID(ctx, portal.ID)
			if err != nil {
				return err
			}
		}
		updates, err := tc.client.API().MessagesGetPollResults(ctx, &tg.MessagesGetPollResultsRequest{
			Peer:  inputPeer,
			MsgID: msgID,
		})
		if err == nil {
			err = tc.handlePollResultUpdates(ctx, msg.ID, updates)
		}
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Str("message_id", string(msg.ID)).Msg("Failed to refresh poll results")
		}
	}
	return nil
}

func (tc *TelegramClient) handlePollResultUpdates(ctx context.Context, messageID networkid.MessageID, updates tg.UpdatesClass) error {
	typedUpdates, ok := updates.(*tg.Updates)
	if !ok {
		return nil
	}
	err := handleUserUpdates[*tg.Updates](ctx, tc, typedUpdates)
	if err != nil {
		return err
	}
	for _, update := range typedUpdates.Updates {
		if pollUpdate, ok := update.(*tg.UpdateMessagePoll); ok {
			err = tc.handlePollResults(ctx, messageID, pollUpdate)
			if err != nil {
				return err
			}
		}
	}
	return nil
}

func (tc *TelegramClient) getPollVotes(ctx context.Context, portalKey networkid.PortalKey, msgID int, results *tg.PollResults) ([]pollVote, bool) {
	answers, ok := results.GetResults()
	if !ok {
		return nil, false
	}
	var mine *pollVote
	if !results.Min {
		mine = &pollVote{Sender: tc.mySender()}
		for _, answer := range answers {
			if answer.Chosen {
				mine.Options = append(mine.Options, answer.Option)
			}
		}
	}

	complete := true
	var votes []pollVote
	voteIndexes := make(map[networkid.UserID]int)
	addVote := func(peer tg.PeerClass, options [][]byte, date int) {
		sender := tc.getPeerSender(peer)
		var ts time.Time
		if date > 0 {
			ts = time.Unix(int64(date), 0)
		}
		if idx, ok := voteIndexes[sender.Sender]; ok {
			votes[idx].Options = append(votes[idx].Options, options...)
		} else {
			voteIndexes[sender.Sender] = len(votes)
			votes = append(votes, pollVote{Sender: sender, Options: options, Timestamp: ts})
		}
	}
	for _, answer := range answers {
		if len(answer.RecentVoters) < answer.Voters {
			complete = false
		}
		for _, voter := range answer.RecentVoters {
			addVote(voter, [][]byte{answer.Option}, 0)
		}
	}
	if !complete {
		votes = votes[:0]
		clear(voteIndexes)
		if mine != nil && len(mine.Options) == 0 {
			return []pollVote{*mine}, false
		}
		votesList, err := tc.fetchPollVotes(ctx, portalKey, msgID)
		if err != nil {
			zerolog.Ctx(ctx).Warn().Err(err).Msg("Failed to get poll votes")
		} else {
			for _, vote := range votesList.Votes {
				switch typedVote := vote.(type) {
				case *tg.MessagePeerVote:
					addVote(typedVote.Peer, [][]byte{typedVote.Option}, typedVote.Date)
				case *tg.MessagePeerVoteMultiple:
					addVote(typedVote.Peer, typedVote.Options, typedVote.Date)
				}
			}
			_, hasMore := votesList.GetNextOffset()
			complete = !hasMore && len(votes) >= votesList.Count
		}
	}
	if mine != nil {
		if idx, ok := voteIndexes[tc.userID]; ok {
			votes[idx].Options = mine.Options
		} else {
			votes = append(votes, *mine)
		}
	}
	return votes, complete
}

func (tc *TelegramClient) fetchPollVotes(ctx context.Context, portalKey networkid.PortalKey, msgID int) (*tg.MessagesVotesList, error) {
	inputPeer, _, err := tc.inputPeerForPortalID(ctx, portalKey.ID)
	if err != nil {
		return nil, err
	}
	return APICallWithUpdates(ctx, tc, func() (*tg.MessagesVotesList, error) {
		return tc.client.API().MessagesGetPollVotes(ctx, &tg.MessagesGetPollVotesRequest{
			Peer:  inputPeer,
			ID:    msgID,
			Limit: 100,
		})
	})
}

func (tc *TelegramClient) syncPollVotes(
	ctx context.Context,
	portal *bridgev2.Portal,
	existing []*database.Message,
	votes []pollVote,
	complete, closed bool,
) (bridgev2.UpsertResult, error) {
	log := zerolog.Ctx(ctx)
	pollIdx := slices.IndexFunc(existing, func(part *database.Message) bool {
		return part.Metadata.(*MessageMetadata).Poll != nil
	})
	if pollIdx < 0 {
		return bridgev2.UpsertResult{}, nil
	}
	pollMsg := existing[pollIdx]
	meta := pollMsg.Metadata.(*MessageMetadata).Poll
	if meta.Ended {
		return bridgev2.UpsertResult{}, nil
	}
	if meta.Votes == nil {
		meta.Votes = make(map[networkid.UserID][]string)
	}
	var changed bool
	updateVote := func(sender bridgev2.EventSender, answers []string, ts time.Time) {
		prev, voted := meta.Votes[sender.Sender]
		if slices.Equal(prev, answers) || (!voted && len(answers) == 0) {
			return
		}
		err := tc.sendPollResponse(ctx, portal, pollMsg, sender, answers, ts)
		if err != nil {
			log.Err(err).Str("voter_id", string(sender.Sender)).Msg("Failed to send poll response")
			return
		}
		if len(answers) == 0 {
			delete(meta.Votes, sender.Sender)
		} else {
			meta.Votes[sender.Sender] = answers
		}
		changed = true
	}

	seen := make(map[networkid.UserID]struct{}, len(votes))
	for _, vote := range votes {
		seen[vote.Sender.Sender] = struct{}{}
		answers := make([]string, len(vote.Options))
		for i, option := range vote.Options {
			answers[i] = pollAnswerID(meta, option)
		}
		slices.Sort(answers)
		updateVote(vote.Sender, answers, vote.Timestamp)
	}
	if complete {
		for userID := range meta.Votes {
			if _, ok := seen[userID]; !ok {
				updateVote(tc.pollVoteSender(userID), nil, time.Time{})
			}
		}
	}
	if closed {
		intent, ok := portal.GetIntentFor(ctx, tc.pollVoteSender(pollMsg.SenderID), tc.userLogin, bridgev2.RemoteEventMessage)
		if ok {
			_, err := intent.SendMessage(ctx, portal.MXID, event.EventUnstablePollEnd, &event.Content{
				Parsed: &event.MessageEventContent{
					RelatesTo: &event.RelatesTo{
						Type:    event.RelReference,
						EventID: pollMsg.MXID,
					},
				},
				Raw: map[string]any{
					"org.matrix.msc3381.poll.end": map[string]any{},
				},
			}, nil)
			if err != nil {
				log.Err(err).Msg("Failed to send poll end")
			} else {
				meta.Ended = true
				changed = true
			}
		}
	}
	return bridgev2.UpsertResult{SaveParts: changed}, nil
}

func (tc *TelegramClient) sendPollResponse(
	ctx context.Context,
	portal *bridgev2.Portal,
	pollMsg *database.Message,
	sender bridgev2.EventSender,
	answers []string,
	ts time.Time,
) error {
	intent, ok := portal.GetIntentFor(ctx, sender, tc.userLogin, bridgev2.RemoteEventMessage)
	if !ok {
		return fmt.Errorf("failed to get intent for voter")
	}
	if answers == nil {
		answers = []string{}
	}
	_, err := intent.SendMessage(ctx, portal.MXID, event.EventUnstablePollResponse, &event.Content{
		Parsed: &event.MessageEventContent{
			RelatesTo: &event.RelatesTo{
				Type:    event.RelReference,
				EventID: pollMsg.MXID,
			},
		},
		Raw: map[string]any{
			"org.matrix.msc3381.poll.response": map[string]any{
				"answers": answers,
			},
		},
	}, &bridgev2.MatrixSendExtra{Timestamp: ts})
	return err
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
			Text:   tg.TextWithEntities{Text: answer.GetText()},
			Option: []byte(strconv.Itoa(i)),
		}
	}
	randomID := parseRandomID(msg.InputTransactionID)
	updates, err := tc.client.API().MessagesSendMedia(ctx, &tg.MessagesSendMediaRequest{
		Peer: peer,
		Media: &tg.InputMediaPoll{
			Poll: tg.Poll{
				PublicVoters:   true,
				MultipleChoice: msg.Content.PollStart.MaxSelections != 1,
				Question:       tg.TextWithEntities{Text: msg.Content.PollStart.Question.GetText()},
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
			pollMeta.MatrixAnswerIDs[pollAnswerID(nil, typedAnswer.Option)] = msg.Content.PollStart.Answers[i].ID
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
		options[i], err = pollOption(pollMeta, answer)
		if err != nil {
			return nil, fmt.Errorf("unknown poll answer ID %q: %w", answer, err)
		}
	}
	updates, err := tc.client.API().MessagesSendVote(ctx, &tg.MessagesSendVoteRequest{
		Peer:    peer,
		MsgID:   tgMsgID,
		Options: options,
	})
	if err != nil {
		return nil, tc.humaniseSendError(err)
	}

	answers := slices.Clone(msg.Content.Response.Answers)
	slices.Sort(answers)
	if pollMeta.Votes == nil {
		pollMeta.Votes = make(map[networkid.UserID][]string)
	}
	if len(answers) == 0 {
		delete(pollMeta.Votes, tc.userID)
	} else {
		pollMeta.Votes[tc.userID] = answers
	}
	err = tc.main.Bridge.DB.Message.Update(ctx, msg.VoteTo)
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Msg("Failed to save own poll vote")
	}

	go func() {
		err := tc.handlePollResultUpdates(ctx, msg.VoteTo.ID, updates)
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Msg("Failed to sync poll votes after voting")
		}
	}()

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
