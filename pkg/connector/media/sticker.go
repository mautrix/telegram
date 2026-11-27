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
	"context"
	"fmt"
	"image"
	_ "image/gif"
	_ "image/png"
	"io"
	"os"
	"strconv"

	"github.com/klauspost/compress/gzip"
	"github.com/rs/zerolog"
	"go.mau.fi/util/ffmpeg"
	"go.mau.fi/util/lottie"

	_ "golang.org/x/image/webp"
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

func (c *AnimatedStickerConfig) Supported(inputMime string) bool {
	if inputMime == "application/x-tgsticker" {
		switch c.Target {
		case "disable":
			return true
		case "gif", "png":
			return lottie.Supported()
		case "webm", "webp":
			return lottie.Supported() && ffmpeg.Supported()
		}
	} else if inputMime == "video/webm" {
		if !c.ConvertFromWebm {
			return true
		}
		switch c.Target {
		case "disable", "webm":
			return true
		case "webp", "png", "gif":
			return ffmpeg.Supported()
		}
	}
	return false
}

func (c *AnimatedStickerConfig) TargetMime(inputMime string) string {
	if c == nil || !c.Supported(inputMime) {
		return ""
	}
	switch inputMime {
	case "application/x-tgsticker":
		switch c.Target {
		case "png":
			return "image/png"
		case "gif":
			return "image/gif"
		case "webm":
			return "video/webm"
		case "webp":
			return "image/webp"
		case "disable":
			return "video/lottie+json"
		default:
			return ""
		}
	case "video/webm":
		if !c.ConvertFromWebm {
			return ""
		}
		switch c.Target {
		case "png":
			return "image/png"
		case "gif":
			return "image/gif"
		case "webp":
			return "image/webp"
		default:
			return ""
		}
	default:
		return ""
	}
}

type ConvertedSticker struct {
	Success           bool
	NewPath           string
	MIMEType          string
	ThumbnailData     []byte
	ThumbnailMIMEType string
	Width             int
	Height            int
	Size              int
}

func (c *AnimatedStickerConfig) convertWebm(ctx context.Context, src *os.File) *ConvertedSticker {
	if !c.ConvertFromWebm || c.Target == "webm" || c.Target == "disable" {
		return nil
	}
	log := zerolog.Ctx(ctx).With().Str("animated_sticker_target", c.Target).Logger()
	if !ffmpeg.Supported() {
		log.Warn().Msg("Not converting webm sticker as ffmpeg is not installed")
		return nil
	}
	var newPath string
	var err error
	switch c.Target {
	case "png":
		newPath, err = ffmpeg.ConvertPath(
			ctx, src.Name(), ".png",
			[]string{"-ss", "0", "-c:v", "libvpx-vp9"},
			[]string{"-frames:v", "1"},
			false,
		)
	case "gif":
		newPath, err = ffmpeg.ConvertPath(
			ctx, src.Name(), ".gif",
			[]string{"-c:v", "libvpx-vp9"},
			[]string{"-vf", "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"},
			false,
		)
	case "webp":
		newPath, err = ffmpeg.ConvertPath(
			ctx, src.Name(), ".webp",
			[]string{"-c:v", "libvpx-vp9"},
			[]string{"-loop", "0"},
			false,
		)
	default:
		log.Error().Msg("Unknown target format for webm conversion")
		return nil
	}
	if err != nil {
		log.Err(err).Msg("Failed to convert webm sticker")
		return nil
	}
	var outputSize int64
	stat, err := os.Stat(newPath)
	if err != nil {
		log.Err(err).Msg("Failed to stat converted sticker")
	} else {
		outputSize = stat.Size()
	}
	width, height := imageSize(newPath)
	if width == 0 || height == 0 {
		width, height = c.Args.Width, c.Args.Height
	} else {
		// Matrix info is the display size. The file stays at its real pixel size,
		// which the spec wants to be twice this for sharp high-DPI rendering.
		width, height = displaySize(width, height, c.Args.Width, c.Args.Height)
	}

	_ = src.Close()
	return &ConvertedSticker{
		Success:  true,
		NewPath:  newPath,
		MIMEType: "image/" + c.Target,
		Width:    width,
		Height:   height,
		Size:     int(outputSize),
	}
}

func displaySize(fileW, fileH, maxW, maxH int) (int, int) {
	if fileW <= 0 || fileH <= 0 || maxW <= 0 || maxH <= 0 || (fileW <= maxW && fileH <= maxH) {
		return fileW, fileH
	}
	if fileW*maxH >= fileH*maxW {
		h := fileH * maxW / fileW
		if h < 1 {
			h = 1
		}
		return maxW, h
	}
	w := fileW * maxH / fileH
	if w < 1 {
		w = 1
	}
	return w, maxH
}

func imageSize(path string) (int, int) {
	f, err := os.Open(path)
	if err != nil {
		return 0, 0
	}
	defer f.Close()
	cfg, _, err := image.DecodeConfig(f)
	if err != nil {
		return 0, 0
	}
	return cfg.Width, cfg.Height
}

func CompressGZip(src *os.File) (replPath string, err error) {
	tempFile, err := os.CreateTemp("", "telegram-sticker-gzip-*.tgs")
	if err != nil {
		return "", fmt.Errorf("failed to create temp file: %w", err)
	}
	writer := gzip.NewWriter(tempFile)
	defer func() {
		_ = tempFile.Close()
		_ = writer.Close()
		if replPath == "" {
			_ = os.Remove(tempFile.Name())
		}
	}()
	_, err = io.Copy(writer, src)
	if err != nil {
		return "", fmt.Errorf("failed to compress lottie gzip: %w", err)
	}
	err = writer.Close()
	if err != nil {
		return "", fmt.Errorf("failed to close gzip writer: %w", err)
	}
	return tempFile.Name(), nil
}

func extractGZip(src *os.File) (*ConvertedSticker, error) {
	reader, err := gzip.NewReader(src)
	if err != nil {
		return nil, fmt.Errorf("failed to create gzip reader: %w", err)
	}
	defer func() {
		_ = reader.Close()
	}()
	replFile, err := os.OpenFile(src.Name()+".json", os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	if err != nil {
		return nil, fmt.Errorf("failed to create temp file: %w", err)
	}
	defer func() {
		_ = replFile.Close()
	}()
	n, err := io.Copy(replFile, reader)
	if err != nil {
		return nil, fmt.Errorf("failed to extract lottie gzip: %w", err)
	}
	return &ConvertedSticker{
		Success:  true,
		NewPath:  replFile.Name(),
		MIMEType: "video/lottie+json",
		Size:     int(n),
	}, nil
}

func (c *AnimatedStickerConfig) convert(ctx context.Context, src *os.File) *ConvertedSticker {
	log := zerolog.Ctx(ctx).With().Str("animated_sticker_target", c.Target).Logger()

	if c.Target == "disable" {
		converted, err := extractGZip(src)
		if err != nil {
			log.Err(err).Msg("Failed to extract lottie sticker")
		}
		return converted
	}

	if !lottie.Supported() {
		log.Warn().Msg("Not converting lottie sticker as lottieconverter is not installed")
		return nil
	} else if (c.Target == "webp" || c.Target == "webm") && !ffmpeg.Supported() {
		log.Warn().Msg("Not converting lottie sticker as target is webp/webm, but ffmpeg is not installed")
		return nil
	}
	outputFilename := src.Name() + "." + c.Target

	var thumbnailData []byte
	var mimeType, thumbnailMIMEType string

	var err error
	switch c.Target {
	case "png":
		mimeType = "image/png"
		err = lottie.Convert(ctx, src, outputFilename, nil, c.Target, c.Args.Width, c.Args.Height, "1")
	case "gif":
		mimeType = "image/gif"
		err = lottie.Convert(ctx, src, outputFilename, nil, c.Target, c.Args.Width, c.Args.Height, strconv.Itoa(c.Args.FPS))
	case "webm", "webp":
		thumbnailMIMEType = "image/png"
		if c.Target == "webm" {
			mimeType = "video/webm"
		} else {
			mimeType = "image/webp"
		}
		thumbnailData, err = lottie.FFmpegConvert(ctx, src, outputFilename, c.Args.Width, c.Args.Height, c.Args.FPS)
		if err != nil {
			break
		}
	default:
		log.Error().Msg("Unknown target format")
		return nil
	}
	if err != nil {
		_ = os.Remove(outputFilename)
		log.Err(err).Msg("Failed to convert animated sticker")
		return nil
	}
	var outputSize int64
	stat, err := os.Stat(outputFilename)
	if err != nil {
		log.Err(err).Msg("Failed to stat converted sticker")
	} else {
		outputSize = stat.Size()
	}

	_ = src.Close()
	return &ConvertedSticker{
		Success:           true,
		NewPath:           outputFilename,
		MIMEType:          mimeType,
		ThumbnailData:     thumbnailData,
		ThumbnailMIMEType: thumbnailMIMEType,
		Width:             c.Args.Width,
		Height:            c.Args.Height,
		Size:              int(outputSize),
	}
}
