package mtproto

import (
	"testing"

	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tmap"
)

func BenchmarkConn_logWithType(b *testing.B) {
	c := Conn{
		log: zap.NewNop(),
		types: tmap.New(map[uint32]string{
			0x3fedd339: "true",
		}),
	}
	buf := bin.Buffer{}
	buf.PutID(0x3fedd339)

	b.ReportAllocs()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		c.logWithType(&buf).Info("Hi!")
	}
}
