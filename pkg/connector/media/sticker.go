package media

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"strconv"

	"github.com/rs/zerolog"
	"go.mau.fi/util/ffmpeg"
	"go.mau.fi/util/lottie"
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
	Data              []byte
	MIMEType          string
	ThumbnailData     []byte
	ThumbnailMIMEType string
	Width             int
	Height            int
}

func (c AnimatedStickerConfig) convert(ctx context.Context, data []byte) ConvertedSticker {
	if c.Target == "disable" {
		return ConvertedSticker{Data: data, MIMEType: "application/x-tgsticker"}
	}

	log := zerolog.Ctx(ctx).With().Str("animated_sticker_target", c.Target).Logger()

	if !lottie.Supported() {
		log.Warn().Msg("lottie not supported, cannot convert animated stickers")
		return ConvertedSticker{Data: data, MIMEType: "application/x-tgsticker"}
	} else if (c.Target == "webp" || c.Target == "webm") && !ffmpeg.Supported() {
		log.Warn().Msg("ffmpeg not supported, cannot convert animated stickers")
		return ConvertedSticker{Data: data, MIMEType: "application/x-tgsticker"}
	}

	input := bytes.NewBuffer(data)
	var convertedData, thumbnailData []byte
	var mimeType, thumbnailMIMEType string

	var err error
	switch c.Target {
	case "png":
		mimeType = "image/png"
		outputWriter := new(bytes.Buffer)
		err = lottie.Convert(ctx, input, "", outputWriter, c.Target, c.Args.Width, c.Args.Height, "1")
		convertedData = outputWriter.Bytes()
	case "gif":
		mimeType = "image/gif"
		outputWriter := new(bytes.Buffer)
		err = lottie.Convert(ctx, input, "", outputWriter, c.Target, c.Args.Width, c.Args.Height, strconv.Itoa(c.Args.FPS))
		convertedData = outputWriter.Bytes()
	case "webm", "webp":
		thumbnailMIMEType = "image/png"
		mimeType = "image/" + c.Target
		thumbnailData, err = lottie.FFmpegConvert(ctx, input, c.Target, c.Args.Width, c.Args.Height, c.Args.FPS)
		if err != nil {
			break
		}
		convertedData, err = os.ReadFile(c.Target)
	default:
		err = fmt.Errorf("unsupported target format %s", c.Target)
	}
	if err != nil {
		log.Err(err).
			Str("target", c.Target).
			Msg("failed to convert animated sticker to target format")

		// Fallback to original data
		return ConvertedSticker{Data: data, MIMEType: "application/x-tgsticker"}
	}

	return ConvertedSticker{
		Data:              convertedData,
		MIMEType:          mimeType,
		ThumbnailData:     thumbnailData,
		ThumbnailMIMEType: thumbnailMIMEType,
		Width:             c.Args.Width,
		Height:            c.Args.Height,
	}

}
