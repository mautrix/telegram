package msgconv

import (
	"context"

	"github.com/gotd/td/telegram"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"
)

type PortalMethods interface {
	DownloadMedia(ctx context.Context, uri id.ContentURIString, file *event.EncryptedFileInfo) ([]byte, error)
	UploadMedia(ctx context.Context, roomID id.RoomID, data []byte, fileName, mimeType string) (url id.ContentURIString, file *event.EncryptedFileInfo, err error)
}

type MessageConverter struct {
	PortalMethods

	Client *telegram.Client
}

func (*MessageConverter) getLogger(ctx context.Context) zerolog.Logger {
	return zerolog.Ctx(ctx).With().Str("component", "message_converter").Logger()
}
