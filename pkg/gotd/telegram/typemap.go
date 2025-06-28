package telegram

import (
	"sync"

	"go.mau.fi/mautrix-telegram/pkg/gotd/mt"
	"go.mau.fi/mautrix-telegram/pkg/gotd/proto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tmap"
)

// Port is default port used by telegram.
const Port = 443

var (
	typesMap  *tmap.Map
	typesOnce sync.Once
)

func getTypesMapping() *tmap.Map {
	typesOnce.Do(func() {
		typesMap = tmap.New(
			tg.TypesMap(),
			mt.TypesMap(),
			proto.TypesMap(),
		)
	})
	return typesMap
}
