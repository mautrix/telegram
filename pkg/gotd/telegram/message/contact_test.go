package message

import (
	"context"
	"testing"
	"unicode/utf8"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message/styling"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestContact(t *testing.T) {
	ctx := context.Background()
	sender, mock := testSender(t)
	contact := tg.InputMediaContact{
		FirstName:   "Михал Палыч",
		LastName:    "Терентьев",
		PhoneNumber: "22 505",
	}

	expectSendMediaAndText(t, &contact, mock, "че с деньгами?", &tg.MessageEntityBold{
		Length: utf8.RuneCountInString("че с деньгами?"),
	})
	_, err := sender.Self().Media(ctx, Contact(contact, styling.Bold("че с деньгами?")))
	require.NoError(t, err)
}
