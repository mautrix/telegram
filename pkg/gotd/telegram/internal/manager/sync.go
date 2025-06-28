package manager

import (
	"sync/atomic"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

// AtomicConfig is atomic tg.Config.
type AtomicConfig struct {
	atomic.Value
}

// NewAtomicConfig creates new AtomicConfig.
func NewAtomicConfig(cfg tg.Config) *AtomicConfig {
	a := &AtomicConfig{}
	a.Store(cfg)
	return a
}

// Load loads atomically config and returns it.
func (c *AtomicConfig) Load() tg.Config {
	return c.Value.Load().(tg.Config)
}

// Store saves given config atomically.
func (c *AtomicConfig) Store(cfg tg.Config) {
	c.Value.Store(cfg)
}
