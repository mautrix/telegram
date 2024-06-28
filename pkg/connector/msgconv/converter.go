package msgconv

import (
	"github.com/gotd/td/telegram"
	"maunium.net/go/mautrix/bridgev2"

	"go.mau.fi/mautrix-telegram/pkg/connector/media"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
)

type MessageConverter struct {
	client                *telegram.Client
	connector             bridgev2.MatrixConnector
	store                 *store.Container
	animatedStickerConfig media.AnimatedStickerConfig

	useDirectMedia bool
}

func NewMessageConverter(client *telegram.Client, connector bridgev2.MatrixConnector, store *store.Container, animatedStickerConfig media.AnimatedStickerConfig, useDirectMedia bool) *MessageConverter {
	return &MessageConverter{
		client:                client,
		connector:             connector,
		store:                 store,
		animatedStickerConfig: animatedStickerConfig,
		useDirectMedia:        useDirectMedia,
	}
}
