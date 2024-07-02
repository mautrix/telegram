package media

import (
	"context"

	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
)

func DownloadDocument(ctx context.Context, client downloader.Client, document *tg.Document) ([]byte, error) {
	data, _, err := DownloadFileLocation(ctx, client, &tg.InputDocumentFileLocation{
		ID:            document.GetID(),
		AccessHash:    document.GetAccessHash(),
		FileReference: document.GetFileReference(),
	})
	return data, err
}
