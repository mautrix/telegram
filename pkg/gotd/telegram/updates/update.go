package updates

import (
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

type update struct {
	Value    any
	State    int
	Count    int
	Entities entities
}

func (u update) start() int { return u.State - u.Count }

func (u update) end() int { return u.State }

// Entities contains update entities.
type entities struct {
	Users []tg.UserClass
	Chats []tg.ChatClass
}

// Merge merges entities.
func (e *entities) Merge(from entities) {
	for _, candidate := range from.Users {
		merge := true
		for _, exist := range e.Users {
			if exist.GetID() == candidate.GetID() {
				merge = false
				break
			}
		}

		if merge {
			e.Users = append(e.Users, candidate)
		}
	}

	for _, candidate := range from.Chats {
		merge := true
		for _, exist := range e.Chats {
			if exist.GetID() == candidate.GetID() {
				merge = false
				break
			}
		}

		if merge {
			e.Chats = append(e.Chats, candidate)
		}
	}
}
