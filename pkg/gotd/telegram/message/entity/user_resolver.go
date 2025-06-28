package entity

import "go.mau.fi/mautrix-telegram/pkg/gotd/tg"

// UserResolver is callback for resolving InputUser by ID.
type UserResolver = func(id int64) (tg.InputUserClass, error)
