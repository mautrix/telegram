package auth

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgmock"
)

func TestClient_AuthBot(t *testing.T) {
	const token = "12345:token"

	t.Run("AuthAuthorization", func(t *testing.T) {
		mock := tgmock.New(t)

		testUser := &tg.User{}
		testUser.SetBot(true)

		mock.ExpectCall(&tg.AuthImportBotAuthorizationRequest{
			BotAuthToken: token,
			APIID:        testAppID,
			APIHash:      testAppHash,
		}).ThenResult(&tg.AuthAuthorization{User: testUser})

		result, err := testClient(mock).Bot(context.Background(), token)
		require.NoError(t, err)
		require.Equal(t, testUser, result.User)
	})

	t.Run("AuthAuthorizationSignUpRequired", func(t *testing.T) {
		mock := tgmock.New(t)

		mock.ExpectCall(&tg.AuthImportBotAuthorizationRequest{
			BotAuthToken: token,
			APIID:        testAppID,
			APIHash:      testAppHash,
		}).ThenResult(&tg.AuthAuthorizationSignUpRequired{})

		result, err := testClient(mock).Bot(context.Background(), token)
		require.Error(t, err)
		require.Nil(t, result)
	})
}
