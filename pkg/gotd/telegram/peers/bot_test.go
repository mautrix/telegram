package peers

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestBot_BotInfo(t *testing.T) {
	a := require.New(t)
	ctx := context.Background()
	mock, m := testManager(t)

	u := m.User(&tg.User{
		Bot:        true,
		ID:         10,
		AccessHash: 10,
		Username:   "thebot",
	})
	b, ok := u.ToBot()
	a.True(ok)

	input := u.InputUser()
	mock.ExpectCall(&tg.UsersGetFullUserRequest{ID: input}).ThenRPCErr(getTestError())

	_, err := b.BotInfo(ctx)
	a.Error(err)

	testUserFull := getTestUserFull()
	testUserFull.ID = u.raw.ID
	{
		i := tg.BotInfo{
			UserID:      u.raw.ID,
			Description: "Test bot",
			Commands:    nil,
			MenuButton:  &tg.BotMenuButtonDefault{},
		}
		i.SetFlags()
		testUserFull.SetBotInfo(i)
	}
	mock.ExpectCall(&tg.UsersGetFullUserRequest{ID: input}).ThenResult(&tg.UsersUserFull{
		FullUser: testUserFull,
	})

	info, err := b.BotInfo(ctx)
	a.NoError(err)
	a.Equal(testUserFull.BotInfo, info)

	// Test caching
	info, err = b.BotInfo(ctx)
	a.NoError(err)
	a.Equal(testUserFull.BotInfo, info)
}
