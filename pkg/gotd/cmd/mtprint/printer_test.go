package main

import (
	"bytes"
	"testing"

	"github.com/stretchr/testify/require"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mt"
	"go.mau.fi/mautrix-telegram/pkg/gotd/proto/codec"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func Test_readAndPrint(t *testing.T) {
	c := codec.Intermediate{}

	input := &bytes.Buffer{}
	buf := &bin.Buffer{}

	objects := []bin.Object{
		&mt.RPCResult{},
		&mt.RPCError{},
		&tg.CodeSettings{},
	}
	for _, o := range objects {
		buf.Reset()
		require.NoError(t, o.Encode(buf))
		require.NoError(t, c.Write(input, buf))
	}

	output := &bytes.Buffer{}
	require.NoError(t, NewPrinter(input, formats("go"), c).Print(output))
	out := output.String()
	require.Contains(t, out, "RPCResult")
	require.Contains(t, out, "RPCError")
	require.Contains(t, out, "CodeSettings")
}
