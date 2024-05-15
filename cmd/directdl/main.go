package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/gotd/td/session"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/id"
)

type FileSession struct{}

func (s *FileSession) LoadSession(context.Context) ([]byte, error) {
	if data, err := os.ReadFile("session"); err != nil {
		return nil, session.ErrNotFound
	} else {
		return data, nil
	}
}

func (s *FileSession) StoreSession(ctx context.Context, data []byte) error {
	return os.WriteFile("session", data, 0600)
}

func main() {
	apiID, err := strconv.ParseInt(os.Args[1], 10, 32)
	if err != nil {
		panic(err)
	}
	apiHash := os.Args[2]

	log.Logger = log.Output(zerolog.ConsoleWriter{Out: os.Stderr})

	zaplog := zap.New(zerozap.New(log.Logger))

	var sessionStorage FileSession
	// https://core.telegram.org/api/obtaining_api_id
	client := telegram.NewClient(int(apiID), apiHash, telegram.Options{
		SessionStorage: &sessionStorage,
		Logger:         zaplog,
	})

	reader := bufio.NewReader(os.Stdin)
	if err := client.Run(context.Background(), func(ctx context.Context) error {
		for {
			fmt.Printf("enter the URI here: ")
			raw, err := reader.ReadString('\n')
			if err != nil {
				panic(err)
			}
			uri, err := id.ParseContentURI(strings.TrimSpace(raw))
			if err != nil {
				panic(err)
			}
			fmt.Printf("uri: %s\n", uri)
			fmt.Printf("fid: %s\n", uri.FileID)
			fmt.Printf("hs: %s\n", uri.Homeserver)

			isPhoto := false
			var id, accessHash int64
			var fileReference []byte
			var thumbSize string
			for _, part := range strings.Split(uri.FileID, ".") {
				if len(part) == 1 && part == "p" {
					isPhoto = true
				} else if len(part) > 1 {
					switch part[0] {
					case 'i':
						id, err = strconv.ParseInt(part[1:], 10, 64)
						if err != nil {
							panic(err)
						}
					case 'a':
						accessHash, err = strconv.ParseInt(part[1:], 10, 64)
						if err != nil {
							panic(err)
						}
					case 'f':
						fileReference, err = base64.RawURLEncoding.DecodeString(part[1:])
						if err != nil {
							panic(err)
						}
					case 't':
						thumbSize = part[1:]
					}
				} else {
					panic("invalid file id")
				}
			}

			if isPhoto {
				file := tg.InputPhotoFileLocation{
					ID:            id,
					AccessHash:    accessHash,
					FileReference: fileReference,
					ThumbSize:     thumbSize,
				}

				fmt.Printf("4\n")
				var buf bytes.Buffer
				if _, err := downloader.NewDownloader().Download(client.API(), &file).Stream(context.TODO(), &buf); err != nil {
					panic(err)
				}
				fmt.Printf("2\n")
				if err := os.WriteFile("/home/sumner/tmp/test.jpg", buf.Bytes(), 0666); err != nil {
					panic(err)
				}
			} else {
				panic("not a photo")
			}
		}
	}); err != nil {
		panic(err)
	}

}
