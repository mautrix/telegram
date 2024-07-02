package media

import (
	"context"
	"fmt"

	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"
)

// LocationToID converts a Telegram [tg.Document],
// [tg.InputDocumentFileLocation], [tg.InputPeerPhotoFileLocation],
// [tg.InputFileLocation], or [tg.InputPhotoFileLocation] into a key for use in
// the telegram_file table.
func LocationToID(location any) (id string) {
	switch location := location.(type) {
	case *tg.Document:
		return fmt.Sprintf("%d", location.ID)
	case *tg.InputDocumentFileLocation:
		return fmt.Sprintf("%d-%s", location.ID, location.ThumbSize)
	case *tg.InputPhotoFileLocation:
		return fmt.Sprintf("%d-%s", location.ID, location.ThumbSize)
	case *tg.InputFileLocation:
		return fmt.Sprintf("%d-%d", location.VolumeID, location.LocalID)
	case *tg.InputPeerPhotoFileLocation:
		return fmt.Sprintf("%d", location.PhotoID)
	default:
		panic(fmt.Errorf("unknown location type %T", location))
	}
}

func TransferToMatrix(ctx context.Context, roomID id.RoomID, client downloader.Client, intent bridgev2.MatrixAPI, file tg.InputFileLocationClass, filenameOpt ...string) (id.ContentURIString, *event.EncryptedFileInfo, error) {
	data, mimeType, err := DownloadFileLocation(ctx, client, file)
	if err != nil {
		return "", nil, fmt.Errorf("downloading file failed: %w", err)
	}
	var filename string
	if len(filenameOpt) > 0 {
		filename = filenameOpt[0]
	}
	return intent.UploadMedia(ctx, roomID, data, filename, mimeType)
}
