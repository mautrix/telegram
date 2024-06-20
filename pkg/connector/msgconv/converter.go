package msgconv

import (
	"github.com/gotd/td/telegram"
	"maunium.net/go/mautrix/bridgev2"
)

type MessageConverter struct {
	client    *telegram.Client
	connector bridgev2.MatrixConnector

	useDirectMedia bool
}

func NewMessageConverter(client *telegram.Client, connector bridgev2.MatrixConnector, useDirectMedia bool) *MessageConverter {
	return &MessageConverter{client: client, connector: connector, useDirectMedia: useDirectMedia}
}
