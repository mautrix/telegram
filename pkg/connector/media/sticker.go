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
	"path/filepath"
	"strconv"

	"github.com/rs/zerolog"
	"go.mau.fi/util/ffmpeg"
	"go.mau.fi/util/lottie"
	"go.mau.fi/util/random"
)

type AnimatedStickerConfig struct {
	Target          string `yaml:"target"`
	ConvertFromWebm bool   `yaml:"convert_from_webm"`
	Args            struct {
		Width  int `yaml:"width"`
		Height int `yaml:"height"`
		FPS    int `yaml:"fps"`
	} `yaml:"args"`
}

type ConvertedSticker struct {
	DataWriter        io.Reader
	MIMEType          string
	ThumbnailData     []byte
	ThumbnailMIMEType string
	Width             int
	Height            int
	Size              int
}

func (c AnimatedStickerConfig) convert(ctx context.Context, data []byte) ConvertedSticker {
	input := bytes.NewBuffer(data)
	if c.Target == "disable" {
		return ConvertedSticker{DataWriter: input, MIMEType: "application/x-tgsticker"}
	}

	log := zerolog.Ctx(ctx).With().Str("animated_sticker_target", c.Target).Logger()

	if !lottie.Supported() {
		log.Warn().Msg("lottie not supported, cannot convert animated stickers")
		return ConvertedSticker{DataWriter: input, MIMEType: "application/x-tgsticker"}
	} else if (c.Target == "webp" || c.Target == "webm") && !ffmpeg.Supported() {
		log.Warn().Msg("ffmpeg not supported, cannot convert animated stickers")
		return ConvertedSticker{DataWriter: input, MIMEType: "application/x-tgsticker"}
	}

	dataWriter := new(bytes.Buffer)
	var thumbnailData []byte
	var mimeType, thumbnailMIMEType string

	var err error
	switch c.Target {
	case "png":
		mimeType = "image/png"
		err = lottie.Convert(ctx, input, "", dataWriter, c.Target, c.Args.Width, c.Args.Height, "1")
	case "gif":
		mimeType = "image/gif"
		err = lottie.Convert(ctx, input, "", dataWriter, c.Target, c.Args.Width, c.Args.Height, strconv.Itoa(c.Args.FPS))
	case "webm", "webp":
		tmpFile := filepath.Join(os.TempDir(), fmt.Sprintf("mautrix-telegram-lottieconverter-%s.%s", random.String(10), c.Target))
		defer func() {
			_ = os.Remove(tmpFile)
		}()
		thumbnailMIMEType = "image/png"
		mimeType = "image/" + c.Target
		thumbnailData, err = lottie.FFmpegConvert(ctx, input, tmpFile, c.Args.Width, c.Args.Height, c.Args.FPS)
		if err != nil {
			break
		}
		var convertedData []byte
		convertedData, err = os.ReadFile(tmpFile)
		dataWriter = bytes.NewBuffer(convertedData)
	default:
		err = fmt.Errorf("unsupported target format %s", c.Target)
	}
	if err != nil {
		log.Err(err).
			Str("target", c.Target).
			Msg("failed to convert animated sticker to target format")

		// Fallback to original data
		return ConvertedSticker{DataWriter: input, MIMEType: "application/x-tgsticker"}
	}

	return ConvertedSticker{
		DataWriter:        dataWriter,
		MIMEType:          mimeType,
		ThumbnailData:     thumbnailData,
		ThumbnailMIMEType: thumbnailMIMEType,
		Width:             c.Args.Width,
		Height:            c.Args.Height,
		Size:              dataWriter.Len(),
	}

}
