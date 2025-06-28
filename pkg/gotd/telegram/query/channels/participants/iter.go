// Package participants contains channel participants iteration helper.
package participants

import (
	"context"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message/peer"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

// Elem is a channel participants iterator element.
type Elem struct {
	Participant tg.ChannelParticipantClass
	Entities    peer.Entities
}

// Iterator is a channel participants stream iterator.
type Iterator struct {
	// Current state.
	lastErr error
	// Buffer state.
	buf    []Elem
	bufCur int
	// Request state.
	limit     int
	lastBatch bool
	// Offset parameters state.
	offset int
	// Remote state.
	count    int
	totalGot bool

	// Query builder.
	query Query
}

// NewIterator creates new iterator.
func NewIterator(query Query, limit int) *Iterator {
	return &Iterator{
		buf:    make([]Elem, 0, limit),
		bufCur: -1,
		limit:  limit,
		query:  query,
	}
}

// Offset sets Offset request parameter.
func (m *Iterator) Offset(offset int) *Iterator {
	m.offset = offset
	return m
}

func (m *Iterator) apply(r tg.ChannelsChannelParticipantsClass) error {
	if m.lastBatch {
		return nil
	}

	var (
		participants tg.ChannelParticipantClassArray
		entities     peer.Entities
	)
	switch prts := r.(type) {
	case *tg.ChannelsChannelParticipants: // channels.channelParticipants#f56ee2a8
		participants = prts.Participants
		entities = peer.NewEntities(prts.MapUsers().UserToMap(), map[int64]*tg.Chat{}, map[int64]*tg.Channel{})

		m.count = prts.Count
		m.lastBatch = len(participants) < 1
	default: // channels.channelParticipantsNotModified#f0173fe9
		return errors.Errorf("unexpected type %T", r)
	}
	m.totalGot = true
	m.offset += len(participants)

	m.bufCur = -1
	m.buf = m.buf[:0]
	for i := range participants {
		m.buf = append(m.buf, Elem{Participant: participants[i], Entities: entities})
	}

	return nil
}

func (m *Iterator) requestNext(ctx context.Context) error {
	r, err := m.query.Query(ctx, Request{
		Offset: m.offset,
		Limit:  m.limit,
	})
	if err != nil {
		return err
	}

	return m.apply(r)
}

func (m *Iterator) bufNext() bool {
	if len(m.buf)-1 <= m.bufCur {
		return false
	}

	m.bufCur++
	return true
}

// Total returns last fetched count of elements.
// If count was not fetched before, it requests server using FetchTotal.
func (m *Iterator) Total(ctx context.Context) (int, error) {
	if m.totalGot {
		return m.count, nil
	}

	return m.FetchTotal(ctx)
}

// FetchTotal fetches and returns count of elements.
func (m *Iterator) FetchTotal(ctx context.Context) (int, error) {
	r, err := m.query.Query(ctx, Request{
		Limit: 1,
	})
	if err != nil {
		return 0, errors.Wrap(err, "fetch total")
	}

	switch prts := r.(type) {
	case *tg.ChannelsChannelParticipants: // channels.channelParticipants#f56ee2a8
		m.count = prts.Count
	default: // channels.channelParticipantsNotModified#f0173fe9
		return 0, errors.Errorf("unexpected type %T", r)
	}

	m.totalGot = true
	return m.count, nil
}

// Next prepares the next message for reading with the Value method.
//
// Returns true on success, or false if there is no next message or an error happened while preparing it.
// Err should be consulted to distinguish between the two cases.
func (m *Iterator) Next(ctx context.Context) bool {
	if m.lastErr != nil {
		return false
	}

	if !m.bufNext() {
		// If buffer is empty, we should fetch next batch.
		if err := m.requestNext(ctx); err != nil {
			m.lastErr = err
			return false
		}
		// Try again with new buffer.
		return m.bufNext()
	}

	return true
}

// Value returns current message.
func (m *Iterator) Value() Elem {
	return m.buf[m.bufCur]
}

// Err returns the error, if any, that was encountered during iteration.
func (m *Iterator) Err() error {
	return m.lastErr
}
