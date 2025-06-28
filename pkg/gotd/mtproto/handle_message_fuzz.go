//go:build fuzz
// +build fuzz

package mtproto

import (
	"time"

	"github.com/go-faster/errors"
	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mt"
	"go.mau.fi/mautrix-telegram/pkg/gotd/proto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/rpc"
	"go.mau.fi/mautrix-telegram/pkg/gotd/testutil"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tmap"
)

type fuzzHandler struct {
	types *tmap.Constructor
}

func (h fuzzHandler) OnMessage(b *bin.Buffer) error {
	id, err := b.PeekID()
	if err != nil {
		return err
	}
	v := h.types.New(id)
	if v == nil {
		return errors.New("not found")
	}
	if err := v.Decode(b); err != nil {
		return errors.Wrap(err, "decode")
	}

	// Performing decode cycle.
	var newBuff bin.Buffer
	newV := h.types.New(id)
	if err := v.Encode(&newBuff); err != nil {
		panic(err)
	}
	if err := newV.Decode(&newBuff); err != nil {
		panic(err)
	}

	return nil
}

func (fuzzHandler) OnSession(session Session) error { return nil }

var (
	conn *Conn
	buf  *bin.Buffer
)

func init() {
	handler := fuzzHandler{
		// Handler will try to dynamically decode any incoming message.
		types: tmap.NewConstructor(
			tg.TypesConstructorMap(),
			mt.TypesConstructorMap(),
		),
	}
	c := &Conn{
		rand:      testutil.ZeroRand{},
		rpc:       rpc.New(rpc.NopSend, rpc.Options{}),
		log:       zap.NewNop(),
		messageID: proto.NewMessageIDGen(time.Now),
		handler:   handler,
	}

	conn = c
	buf = &bin.Buffer{}
}

func FuzzHandleMessage(data []byte) int {
	buf.ResetTo(data)
	if err := conn.handleMessage(buf); err != nil {
		return 0
	}
	return 1
}
