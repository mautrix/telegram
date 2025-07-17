// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Automattic Inc.
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
	"sync"
)

type Future[T any] struct {
	value T
	err   error
	ready chan struct{}
	once  sync.Once
}

func NewFuture[T any]() *Future[T] {
	return &Future[T]{
		ready: make(chan struct{}),
	}
}

func (f *Future[T]) Set(value T) {
	f.once.Do(func() {
		f.value = value
		close(f.ready)
	})
}

func (f *Future[T]) Get(ctx context.Context) (T, error) {
	select {
	case <-f.ready:
		return f.value, nil
	case <-ctx.Done():
		return f.value, ctx.Err()
	}
}
