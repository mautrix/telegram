package e2e

import (
	"context"
	"sync"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/updates"
)

var _ updates.StateStorage = (*memStorage)(nil)

type memStorage struct {
	states   map[int64]updates.State
	channels map[int64]map[int64]int
	mux      sync.Mutex
}

func newMemStorage() *memStorage {
	return &memStorage{
		states:   map[int64]updates.State{},
		channels: map[int64]map[int64]int{},
	}
}

func (s *memStorage) GetState(ctx context.Context, userID int64) (state updates.State, found bool, err error) {
	s.mux.Lock()
	defer s.mux.Unlock()

	state, found = s.states[userID]
	return
}

func (s *memStorage) SetState(ctx context.Context, userID int64, state updates.State) error {
	s.mux.Lock()
	defer s.mux.Unlock()

	s.states[userID] = state
	s.channels[userID] = map[int64]int{}
	return nil
}

func (s *memStorage) SetPts(ctx context.Context, userID int64, pts int) error {
	s.mux.Lock()
	defer s.mux.Unlock()

	state, ok := s.states[userID]
	if !ok {
		return errors.New("state not found")
	}

	state.Pts = pts
	s.states[userID] = state
	return nil
}

func (s *memStorage) SetQts(ctx context.Context, userID int64, qts int) error {
	s.mux.Lock()
	defer s.mux.Unlock()

	state, ok := s.states[userID]
	if !ok {
		return errors.New("state not found")
	}

	state.Qts = qts
	s.states[userID] = state
	return nil
}

func (s *memStorage) SetDate(ctx context.Context, userID int64, date int) error {
	s.mux.Lock()
	defer s.mux.Unlock()

	state, ok := s.states[userID]
	if !ok {
		return errors.New("state not found")
	}

	state.Date = date
	s.states[userID] = state
	return nil
}

func (s *memStorage) SetSeq(ctx context.Context, userID int64, seq int) error {
	s.mux.Lock()
	defer s.mux.Unlock()

	state, ok := s.states[userID]
	if !ok {
		return errors.New("state not found")
	}

	state.Seq = seq
	s.states[userID] = state
	return nil
}

func (s *memStorage) SetDateSeq(ctx context.Context, userID int64, date, seq int) error {
	s.mux.Lock()
	defer s.mux.Unlock()

	state, ok := s.states[userID]
	if !ok {
		return errors.New("state not found")
	}

	state.Date = date
	state.Seq = seq
	s.states[userID] = state
	return nil
}

func (s *memStorage) SetChannelPts(ctx context.Context, userID, channelID int64, pts int) error {
	s.mux.Lock()
	defer s.mux.Unlock()

	channels, ok := s.channels[userID]
	if !ok {
		return errors.New("user state does not exist")
	}

	channels[channelID] = pts
	return nil
}

func (s *memStorage) GetChannelPts(ctx context.Context, userID, channelID int64) (pts int, found bool, err error) {
	s.mux.Lock()
	defer s.mux.Unlock()

	channels, ok := s.channels[userID]
	if !ok {
		return 0, false, nil
	}

	pts, found = channels[channelID]
	return
}

func (s *memStorage) ForEachChannels(ctx context.Context, userID int64, f func(ctx context.Context, channelID int64, pts int) error) error {
	s.mux.Lock()
	defer s.mux.Unlock()

	cmap, ok := s.channels[userID]
	if !ok {
		return errors.New("channels map does not exist")
	}

	for id, pts := range cmap {
		if err := f(ctx, id, pts); err != nil {
			return err
		}
	}

	return nil
}

var _ updates.AccessHasher = (*memAccessHasher)(nil)

type memAccessHasher struct {
	channelHashes map[int64]map[int64]int64
	userHashes    map[int64]map[int64]int64
	mux           sync.Mutex
}

func newMemAccessHasher() *memAccessHasher {
	return &memAccessHasher{
		channelHashes: map[int64]map[int64]int64{},
		userHashes:    map[int64]map[int64]int64{},
	}
}

func (m *memAccessHasher) GetChannelAccessHash(ctx context.Context, forUserID, channelID int64) (accessHash int64, found bool, err error) {
	m.mux.Lock()
	defer m.mux.Unlock()

	accessHashes, ok := m.channelHashes[forUserID]
	if !ok {
		return 0, false, nil
	}

	accessHash, found = accessHashes[channelID]
	return
}

func (m *memAccessHasher) SetChannelAccessHash(ctx context.Context, forUserID, channelID, accessHash int64) error {
	m.mux.Lock()
	defer m.mux.Unlock()

	accessHashes, ok := m.channelHashes[forUserID]
	if !ok {
		accessHashes = map[int64]int64{}
		m.channelHashes[forUserID] = accessHashes
	}

	accessHashes[channelID] = accessHash
	return nil
}

func (m *memAccessHasher) GetUserAccessHash(ctx context.Context, forUserID, userID int64) (accessHash int64, found bool, err error) {
	m.mux.Lock()
	defer m.mux.Unlock()

	accessHashes, ok := m.userHashes[forUserID]
	if !ok {
		return 0, false, nil
	}

	accessHash, found = accessHashes[userID]
	return
}

func (m *memAccessHasher) SetUserAccessHash(ctx context.Context, forUserID, userID, accessHash int64) error {
	m.mux.Lock()
	defer m.mux.Unlock()

	accessHashes, ok := m.userHashes[forUserID]
	if !ok {
		accessHashes = map[int64]int64{}
		m.channelHashes[forUserID] = accessHashes
	}

	accessHashes[userID] = accessHash
	return nil
}
