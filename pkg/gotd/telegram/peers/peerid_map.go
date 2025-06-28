package peers

import (
	"go.mau.fi/mautrix-telegram/pkg/gotd/constant"
)

type peerIDSet struct {
	m map[constant.TDLibPeerID]struct{}
}

func (p *peerIDSet) add(ids ...constant.TDLibPeerID) {
	for _, id := range ids {
		p.m[id] = struct{}{}
	}
}

func (p *peerIDSet) delete(ids ...constant.TDLibPeerID) {
	for _, id := range ids {
		delete(p.m, id)
	}
}

func (p *peerIDSet) has(id constant.TDLibPeerID) bool {
	_, ok := p.m[id]
	return ok
}
