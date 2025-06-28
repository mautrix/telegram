// Package td implements MTProto encoding and decoding.
package td

//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/gotdgen --doc "https://core.telegram.org/" --clean --server --handlers --mapping --slices --package tg --target tg --schema _schema/telegram.tl
//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/gotdgen --doc "https://core.telegram.org/" --clean --package e2e --target tg/e2e --schema _schema/encrypted.tl

//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/gotdgen --clean --package tdapi --tdlib-json --target tdapi --schema _schema/tdapi.tl

//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/gotdgen --clean --client=false --package tgtrace --target tgtrace --schema _schema/trace.tl

//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/gotdgen --clean --package mt --target mt --client=false --schema _schema/mt.tl
