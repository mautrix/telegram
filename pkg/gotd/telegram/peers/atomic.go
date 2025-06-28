package peers

import (
	"go.uber.org/atomic"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

type atomicUser struct {
	value atomic.Value // holds *tg.User
}

func (u *atomicUser) Load() (*tg.User, bool) {
	v, ok := u.value.Load().(*tg.User)
	return v, ok
}

func (u *atomicUser) Store(user *tg.User) {
	u.value.Store(user)
}
