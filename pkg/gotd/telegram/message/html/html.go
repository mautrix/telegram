// Package html contains HTML styling options.
package html

import (
	"bytes"
	"fmt"
	"io"
	"strings"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message/entity"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message/styling"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

// Bytes reads HTML from given byte slice and returns styling option
// to build styled text block.
func Bytes(resolver func(id int64) (tg.InputUserClass, error), b []byte) styling.StyledTextOption {
	return Reader(resolver, bytes.NewReader(b))
}

// String reads HTML from given string and returns styling option
// to build styled text block.
func String(resolver func(id int64) (tg.InputUserClass, error), s string) styling.StyledTextOption {
	return Reader(resolver, strings.NewReader(s))
}

// Format formats string using fmt, parses HTML from formatted string and returns styling option
// to build styled text block.
func Format(resolver func(id int64) (tg.InputUserClass, error), format string, args ...interface{}) styling.StyledTextOption {
	return styling.Custom(func(eb *entity.Builder) error {
		var buf bytes.Buffer
		_, err := fmt.Fprintf(&buf, format, args...)
		if err != nil {
			return err
		}
		return HTML(&buf, eb, Options{
			UserResolver: resolver,
		})
	})
}

// Reader reads HTML from given reader and returns styling option
// to build styled text block.
func Reader(resolver func(id int64) (tg.InputUserClass, error), r io.Reader) styling.StyledTextOption {
	return styling.Custom(func(eb *entity.Builder) error {
		return HTML(r, eb, Options{
			UserResolver: resolver,
		})
	})
}
