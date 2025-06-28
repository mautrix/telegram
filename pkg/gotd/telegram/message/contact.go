package message

import "go.mau.fi/mautrix-telegram/pkg/gotd/tg"

// Contact adds contact attachment.
func Contact(contact tg.InputMediaContact, caption ...StyledTextOption) MediaOption {
	return Media(&contact, caption...)
}
