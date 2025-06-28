package message

import "go.mau.fi/mautrix-telegram/pkg/gotd/tg"

// Game adds a game attachment.
func Game(id tg.InputGameClass, caption ...StyledTextOption) MediaOption {
	return Media(&tg.InputMediaGame{
		ID: id,
	}, caption...)
}
