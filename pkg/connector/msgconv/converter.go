package msgconv

import "github.com/gotd/td/telegram"

type MessageConverter struct {
	client *telegram.Client

	useDirectMedia bool
}

func NewMessageConverter(client *telegram.Client, useDirectMedia bool) *MessageConverter {
	return &MessageConverter{client: client, useDirectMedia: useDirectMedia}
}
