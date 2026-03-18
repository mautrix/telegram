// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

package media

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"
	"maunium.net/go/mautrix/mediaproxy"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/downloader"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

type dimensionable interface {
	GetW() int
	GetH() int
}

func getLargestPhotoSize(sizes []tg.PhotoSizeClass) (width, height, fileSize int, largest tg.PhotoSizeClass) {
	if len(sizes) == 0 {
		panic("cannot get largest size from empty list of sizes")
	}

	// FIXME this max size seems to be confusing bytes and dimensions.
	for _, s := range sizes {
		var currentSize int
		switch size := s.(type) {
		case *tg.PhotoSize:
			currentSize = size.GetSize()
		case *tg.PhotoCachedSize:
			currentSize = max(size.W, size.H, len(size.Bytes))
		case *tg.PhotoSizeProgressive:
			currentSize = max(size.W, size.H)
			for _, sz := range size.Sizes {
				currentSize = max(currentSize, sz)
			}
		case *tg.PhotoPathSize:
			currentSize = len(size.GetBytes())
		case *tg.PhotoStrippedSize:
			currentSize = len(size.GetBytes())
		}

		if currentSize > fileSize {
			fileSize = currentSize
			largest = s
			if d, ok := s.(dimensionable); ok {
				width = d.GetW()
				height = d.GetH()
			}
		}
	}
	return
}

// getLocationID converts a Telegram [tg.Document],
// [tg.InputDocumentFileLocation], [tg.InputPeerPhotoFileLocation],
// [tg.InputFileLocation], or [tg.InputPhotoFileLocation] into a [LocationID]
// for use in the telegram_file table.
func getLocationID(loc any) (locID store.TelegramFileLocationID) {
	var id string
	switch location := loc.(type) {
	case *tg.Document:
		id = fmt.Sprintf("%d", location.ID)
	case *tg.InputDocumentFileLocation:
		id = fmt.Sprintf("%d-%s", location.ID, location.ThumbSize)
	case *tg.InputPhotoFileLocation:
		id = fmt.Sprintf("%d-%s", location.ID, location.ThumbSize)
	case *tg.InputFileLocation:
		id = fmt.Sprintf("%d-%d", location.VolumeID, location.LocalID)
	case *tg.InputPeerPhotoFileLocation:
		id = fmt.Sprintf("%d", location.PhotoID)
	default:
		panic(fmt.Errorf("unknown location type %T", location))
	}
	return store.TelegramFileLocationID(id)
}

// Transferer is a utility for downloading media from Telegram and uploading it
// to Matrix.
type Transferer struct {
	client downloader.Client

	roomID                id.RoomID
	filename              string
	animatedStickerConfig *AnimatedStickerConfig

	fileInfo event.FileInfo
}

type ReadyTransferer struct {
	inner *Transferer
	loc   tg.InputFileLocationClass
}

// NewTransferer creates a new [Transferer] with the given [downloader.Client].
// The client is used to download the media from Telegram.
func NewTransferer(client downloader.Client) *Transferer {
	return &Transferer{client: client}
}

// WithRoomID sets the room ID for the [Transferer].
func (t *Transferer) WithRoomID(roomID id.RoomID) *Transferer {
	t.roomID = roomID
	return t
}

// WithFilename sets the filename for the [Transferer].
func (t *Transferer) WithFilename(filename string) *Transferer {
	t.filename = filename
	return t
}

// WithStickerConfig sets the animated sticker config for the [Transferer].
func (t *Transferer) WithStickerConfig(cfg AnimatedStickerConfig) *Transferer {
	t.animatedStickerConfig = &cfg
	t.adjustStickerSize()
	return t
}

func (t *Transferer) adjustStickerSize() {
	if (t.fileInfo.Width < 256 && t.fileInfo.Height < 256) || t.animatedStickerConfig == nil {
		return
	}
	if t.fileInfo.Width == t.fileInfo.Height {
		t.fileInfo.Width, t.fileInfo.Height = 256, 256
	} else if t.fileInfo.Width > t.fileInfo.Height {
		t.fileInfo.Height = t.fileInfo.Height * 256 / t.fileInfo.Width
		t.fileInfo.Width = 256
	} else {
		t.fileInfo.Width = t.fileInfo.Width * 256 / t.fileInfo.Height
		t.fileInfo.Height = 256
	}
}

func (t *Transferer) WithMIMEType(mimeType string) *Transferer {
	t.fileInfo.MimeType = mimeType
	return t
}

func (t *Transferer) WithThumbnail(uri id.ContentURIString, file *event.EncryptedFileInfo, info *event.FileInfo) *Transferer {
	t.fileInfo.ThumbnailURL = uri
	t.fileInfo.ThumbnailFile = file
	t.fileInfo.ThumbnailInfo = info
	return t
}

func (t *Transferer) WithVideo(attr *tg.DocumentAttributeVideo) *Transferer {
	t.fileInfo.Width, t.fileInfo.Height = attr.W, attr.H
	t.fileInfo.Duration = int(attr.Duration * 1000)
	return t
}

func (t *Transferer) WithImageSize(attr *tg.DocumentAttributeImageSize) *Transferer {
	t.fileInfo.Width, t.fileInfo.Height = attr.W, attr.H
	t.adjustStickerSize()
	return t
}

// WithDocument transforms a [Transferer] to a [ReadyTransferer] by setting the
// given document as the location that will be downloaded by the
// [ReadyTransferer].
func (t *Transferer) WithDocument(doc tg.DocumentClass, thumbnail bool) *ReadyTransferer {
	document := doc.(*tg.Document)
	documentFileLocation := tg.InputDocumentFileLocation{
		ID:            document.GetID(),
		AccessHash:    document.GetAccessHash(),
		FileReference: document.GetFileReference(),
	}
	if thumbnail {
		_, _, _, largestThumbnail := getLargestPhotoSize(document.Thumbs)
		documentFileLocation.ThumbSize = largestThumbnail.GetType()
	} else {
		t.fileInfo.Size = int(document.Size)
		if t.fileInfo.MimeType == "" {
			t.fileInfo.MimeType = document.GetMimeType()
		}
	}
	return &ReadyTransferer{t, &documentFileLocation}
}

// WithPhoto transforms a [Transferer] to a [ReadyTransferer] by setting the
// given photo as the location that will be downloaded by the
// [ReadyTransferer].
func (t *Transferer) WithPhoto(pc tg.PhotoClass) *ReadyTransferer {
	photo := pc.(*tg.Photo)
	var largest tg.PhotoSizeClass
	t.fileInfo.Width, t.fileInfo.Height, t.fileInfo.Size, largest = getLargestPhotoSize(photo.GetSizes())
	// All photos are jpeg in Telegram
	t.fileInfo.MimeType = "image/jpeg"
	return &ReadyTransferer{
		inner: t,
		loc: &tg.InputPhotoFileLocation{
			ID:            photo.GetID(),
			AccessHash:    photo.GetAccessHash(),
			FileReference: photo.GetFileReference(),
			ThumbSize:     largest.GetType(),
		},
	}
}

// WithUserPhoto transforms a [Transferer] to a [ReadyTransferer] by setting
// the given user's photo as the location that will be downloaded by the
// [ReadyTransferer].
func (t *Transferer) WithUserPhoto(ctx context.Context, store *store.ScopedStore, userID int64, photoID int64) (*ReadyTransferer, error) {
	if accessHash, err := store.GetAccessHash(ctx, ids.PeerTypeUser, userID); err != nil {
		return nil, fmt.Errorf("failed to get user access hash for %d: %w", userID, err)
	} else {
		return t.WithPeerPhoto(&tg.InputPeerUser{UserID: userID, AccessHash: accessHash}, photoID), nil
	}
}

// WithChannelPhoto transforms a [Transferer] to a [ReadyTransferer] by setting
// the given channel's photo as the location that will be downloaded by the
// [ReadyTransferer].
func (t *Transferer) WithChannelPhoto(ctx context.Context, store *store.ScopedStore, channelID int64, photoID int64) (*ReadyTransferer, error) {
	if accessHash, err := store.GetAccessHash(ctx, ids.PeerTypeChannel, channelID); err != nil {
		return nil, fmt.Errorf("failed to get channel access hash for %d: %w", channelID, err)
	} else {
		return t.WithPeerPhoto(&tg.InputPeerChannel{ChannelID: channelID, AccessHash: accessHash}, photoID), nil
	}
}

// WithPeerPhoto transforms a [Transferer] to a [ReadyTransferer] by setting
// the given user, chat or channel photo as the location that will be downloaded by the
// [ReadyTransferer].
func (t *Transferer) WithPeerPhoto(peer tg.InputPeerClass, photoID int64) *ReadyTransferer {
	return &ReadyTransferer{
		inner: t,
		loc: &tg.InputPeerPhotoFileLocation{
			Peer:    peer,
			PhotoID: photoID,
			Big:     true,
		},
	}
}

// Transfer downloads the media from Telegram and uploads it to Matrix.
//
// If the file is already in the database, the MXC URI will be reused. The
// file's MXC URI will only be cached if the room ID is unset or if the room is
// not encrypted.
//
// If there is a sticker config on the [Transferer], this function converts
// animated stickers to the target format specified by the specified
// [AnimatedStickerConfig].
func (t *ReadyTransferer) Transfer(ctx context.Context, store *store.Container, intent bridgev2.MatrixAPI) (mxc id.ContentURIString, encryptedFileInfo *event.EncryptedFileInfo, outFileInfo *event.FileInfo, err error) {
	locationID := getLocationID(t.loc)
	log := zerolog.Ctx(ctx).With().
		Str("component", "media_transfer").
		Str("location_id", string(locationID)).
		Logger()
	ctx = log.WithContext(ctx)
	log.Debug().Msg("Transferring file from Telegram to Matrix")

	if file, err := store.TelegramFile.GetByLocationID(ctx, locationID); err != nil {
		return "", nil, nil, fmt.Errorf("failed to search for Telegram file by location ID: %w", err)
	} else if file != nil {
		t.inner.fileInfo.Size, t.inner.fileInfo.MimeType = file.Size, file.MIMEType
		return file.MXC, nil, &t.inner.fileInfo, nil
	}

	var reader io.Reader
	reader, t.inner.fileInfo.MimeType, t.inner.fileInfo.Size, err = t.Stream(ctx)
	if err != nil {
		return "", nil, nil, fmt.Errorf("downloading file failed: %w", err)
	}

	needStickerConvert := t.inner.animatedStickerConfig != nil && (t.inner.fileInfo.MimeType == "application/x-tgsticker" ||
		(t.inner.fileInfo.MimeType == "video/webm" && t.inner.animatedStickerConfig.ConvertFromWebm && t.inner.animatedStickerConfig.Target != "webm"))

	var thumbnailData []byte
	var thumbnailMIMEType string
	mxc, encryptedFileInfo, err = intent.UploadMediaStream(ctx, t.inner.roomID, int64(t.inner.fileInfo.Size), needStickerConvert, func(file io.Writer) (*bridgev2.FileStreamResult, error) {
		_, err := io.Copy(file, reader)
		if err != nil {
			return nil, fmt.Errorf("failed to stream download: %w", err)
		}
		var replacementFile string
		if needStickerConvert {
			osFile := file.(*os.File)
			_, err = osFile.Seek(0, io.SeekStart)
			if err != nil {
				return nil, fmt.Errorf("failed to seek to start of file for sticker conversion: %w", err)
			}
			var converted *ConvertedSticker
			if t.inner.fileInfo.MimeType == "video/webm" {
				converted = t.inner.animatedStickerConfig.convertWebm(ctx, osFile)
			} else {
				t.inner.fileInfo.MimeType = "application/x-tgsticker" // This is expected to get overridden by convert
				converted = t.inner.animatedStickerConfig.convert(ctx, osFile)
			}
			if converted != nil {
				replacementFile = converted.NewPath
				t.inner.fileInfo.MimeType = converted.MIMEType
				t.inner.fileInfo.Width = converted.Width
				t.inner.fileInfo.Height = converted.Height
				t.inner.fileInfo.Size = converted.Size
				thumbnailData = converted.ThumbnailData
				thumbnailMIMEType = converted.ThumbnailMIMEType
			}
		}
		return &bridgev2.FileStreamResult{
			FileName:        t.inner.filename,
			MimeType:        t.inner.fileInfo.MimeType,
			ReplacementFile: replacementFile,
		}, err
	})
	if err != nil {
		return "", nil, nil, fmt.Errorf("failed to upload media to Matrix: %w", err)
	}
	if thumbnailData != nil {
		thumbnailMXC, thumbnailFileInfo, err := intent.UploadMedia(ctx, t.inner.roomID, thumbnailData, t.inner.filename, thumbnailMIMEType)
		if err != nil {
			log.Err(err).Msg("failed to upload animated sticker thumbnail to Matrix")
		} else {
			t.inner = t.inner.WithThumbnail(thumbnailMXC, thumbnailFileInfo, &event.FileInfo{
				MimeType: thumbnailMIMEType,
				Width:    t.inner.fileInfo.Width,
				Height:   t.inner.fileInfo.Height,
				Size:     len(thumbnailData),
			})
		}
	}

	// If it's an unencrypted file, cache the MXC URI corresponding to the
	// location ID.
	if len(mxc) > 0 {
		file := store.TelegramFile.New()
		file.LocationID = locationID
		file.MXC = mxc
		file.Size = t.inner.fileInfo.Size
		file.MIMEType = t.inner.fileInfo.MimeType
		if err = file.Insert(ctx); err != nil {
			log.Err(err).Msg("failed to insert Telegram file into database")
		}
	}
	return mxc, encryptedFileInfo, &t.inner.fileInfo, nil
}

// Stream streams the media from Telegram to an [io.Reader].
func (t *ReadyTransferer) Stream(ctx context.Context) (r io.Reader, mimeType string, fileSize int, err error) {
	var storageFileTypeClass tg.StorageFileTypeClass
	storageFileTypeClass, r, err = downloader.NewDownloader().WithPartSize(1024*1024).Download(t.inner.client, t.loc).StreamToReader(ctx)
	if err != nil {
		return nil, "", 0, err
	}
	if t.inner.fileInfo.MimeType == "" || t.inner.fileInfo.MimeType == "application/octet-stream" {
		switch storageFileTypeClass.(type) {
		case *tg.StorageFileJpeg:
			t.inner.fileInfo.MimeType = "image/jpeg"
		case *tg.StorageFileGif:
			t.inner.fileInfo.MimeType = "image/gif"
		case *tg.StorageFilePng:
			t.inner.fileInfo.MimeType = "image/png"
		case *tg.StorageFilePdf:
			t.inner.fileInfo.MimeType = "application/pdf"
		case *tg.StorageFileMp3:
			t.inner.fileInfo.MimeType = "audio/mpeg"
		case *tg.StorageFileMov:
			t.inner.fileInfo.MimeType = "video/quicktime"
		case *tg.StorageFileMp4:
			t.inner.fileInfo.MimeType = "video/mp4"
		case *tg.StorageFileWebp:
			t.inner.fileInfo.MimeType = "image/webp"
		default:
			t.inner.fileInfo.MimeType = "application/octet-stream"
		}
	}

	return r, t.inner.fileInfo.MimeType, t.inner.fileInfo.Size, nil
}

func (t *ReadyTransferer) ToDirectMediaResponse(ctx context.Context) (mediaproxy.GetMediaResponse, error) {
	if t == nil {
		return nil, fmt.Errorf("invalid direct media request")
	}
	log := zerolog.Ctx(ctx)
	r, mimeType, size, err := t.Stream(ctx)
	if err != nil {
		log.Err(err).Msg("Failed to download media")
		return nil, err
	}
	log.Debug().
		Str("mime_type", mimeType).
		Int("size", size).
		Msg("Started downloading media successfully")

	if t.inner.animatedStickerConfig != nil {
		return &mediaproxy.GetMediaResponseFile{
			Callback: func(w *os.File) (*mediaproxy.FileMeta, error) {
				_, err = io.Copy(w, r)
				if err != nil {
					return nil, fmt.Errorf("failed to write animated sticker data to file: %w", err)
				}
				_, err = w.Seek(0, io.SeekStart)
				if err != nil {
					return nil, fmt.Errorf("failed to seek to start of file for sticker conversion: %w", err)
				}
				var converted *ConvertedSticker
				if t.inner.fileInfo.MimeType == "video/webm" {
					converted = t.inner.animatedStickerConfig.convertWebm(ctx, w)
				} else {
					t.inner.fileInfo.MimeType = "application/x-tgsticker" // This is expected to get overridden by convert
					converted = t.inner.animatedStickerConfig.convert(ctx, w)
				}
				if converted == nil {
					return &mediaproxy.FileMeta{ContentType: t.inner.fileInfo.MimeType}, nil
				}
				return &mediaproxy.FileMeta{
					ContentType:     converted.MIMEType,
					ReplacementFile: converted.NewPath,
				}, nil
			},
		}, nil
	}

	return &mediaproxy.GetMediaResponseData{
		Reader:        io.NopCloser(r),
		ContentType:   mimeType,
		ContentLength: int64(size),
	}, nil
}

// DownloadBytes downloads the media from Telegram to a byte buffer.
func (t *ReadyTransferer) DownloadBytes(ctx context.Context) ([]byte, error) {
	var buf bytes.Buffer
	_, err := downloader.NewDownloader().Download(t.inner.client, t.loc).Stream(ctx, &buf)
	return buf.Bytes(), err
}

// DirectDownloadURL returns the direct download URL for the media.
func (t *ReadyTransferer) DirectDownloadURL(ctx context.Context, loggedInUserID int64, portal *bridgev2.Portal, msgID int, thumbnail bool, telegramMediaID int64) (id.ContentURIString, *event.FileInfo, error) {
	peerType, chatID, _, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		return "", nil, err
	}
	mediaID, err := ids.DirectMediaInfo{
		PeerType:  peerType,
		PeerID:    chatID,
		UserID:    loggedInUserID,
		MessageID: int64(msgID),
		Thumbnail: thumbnail,
		ID:        telegramMediaID,
	}.AsMediaID()
	if err != nil {
		return "", nil, err
	}
	mxc, err := portal.Bridge.Matrix.GenerateContentURI(ctx, mediaID)
	if t.inner.fileInfo.MimeType == "" {
		t.inner.fileInfo.MimeType = "application/octet-stream"
	}
	if t.inner.fileInfo.MimeType == "application/x-tgsticker" {
		t.inner.fileInfo.MimeType = "video/lottie+json"
	}
	return mxc, &t.inner.fileInfo, err
}
