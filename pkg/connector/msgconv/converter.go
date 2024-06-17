package msgconv

import "github.com/gotd/td/telegram"

type MessageConverter struct {
	client *telegram.Client
}

func NewMessageConverter(client *telegram.Client) *MessageConverter {
	return &MessageConverter{client: client}
}
