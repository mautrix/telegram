package telegram

import (
	"context"
	"fmt"

	"github.com/go-faster/errors"
	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/crypto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mtproto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/pool"
	"go.mau.fi/mautrix-telegram/pkg/gotd/session"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func (c *Client) restoreConnection(ctx context.Context) error {
	if c.storage == nil {
		return nil
	}

	data, err := c.storage.Load(ctx)
	if errors.Is(err, session.ErrNotFound) {
		return nil
	}
	if err != nil {
		return errors.Wrap(err, "load")
	}

	// If file does not contain DC ID, so we use DC from options.
	prev := c.session.Load()
	if data.DC == 0 {
		data.DC = prev.DC
	}

	// Restoring persisted auth key.
	var key crypto.AuthKey
	copy(key.Value[:], data.AuthKey)
	copy(key.ID[:], data.AuthKeyID)

	if key.Value.ID() != key.ID {
		return errors.New("corrupted key")
	}

	// Re-initializing connection from persisted state.
	c.log.Info("Connection restored from state",
		zap.String("addr", data.Addr),
		zap.String("key_id", fmt.Sprintf("%x", data.AuthKeyID)),
	)

	c.connMux.Lock()
	c.session.Store(pool.Session{
		DC:      data.DC,
		AuthKey: key,
		Salt:    data.Salt,
	})
	c.conn = c.createPrimaryConn(nil)
	c.connMux.Unlock()

	return nil
}

func (c *Client) saveSession(cfg tg.Config, s mtproto.Session) error {
	if c.storage == nil {
		return nil
	}

	data, err := c.storage.Load(c.ctx)
	if errors.Is(err, session.ErrNotFound) {
		// Initializing new state.
		err = nil
		data = &session.Data{}
	}
	if err != nil {
		return errors.Wrap(err, "load")
	}

	// Updating previous data.
	data.Config = session.ConfigFromTG(cfg)
	data.AuthKey = s.Key.Value[:]
	data.AuthKeyID = s.Key.ID[:]
	data.DC = cfg.ThisDC
	data.Salt = s.Salt

	if err := c.storage.Save(c.ctx, data); err != nil {
		return errors.Wrap(err, "save")
	}

	c.log.Debug("Data saved",
		zap.String("key_id", fmt.Sprintf("%x", data.AuthKeyID)),
	)
	return nil
}

func (c *Client) onSession(cfg tg.Config, s mtproto.Session) error {
	c.sessionsMux.Lock()
	c.sessions[cfg.ThisDC] = pool.NewSyncSession(pool.Session{
		DC:      cfg.ThisDC,
		Salt:    s.Salt,
		AuthKey: s.Key,
	})
	c.sessionsMux.Unlock()

	primaryDC := c.session.Load().DC
	// Do not save session for non-primary DC.
	if cfg.ThisDC != 0 && primaryDC != 0 && primaryDC != cfg.ThisDC {
		return nil
	}

	c.connMux.Lock()
	c.session.Store(pool.Session{
		DC:      cfg.ThisDC,
		Salt:    s.Salt,
		AuthKey: s.Key,
	})
	c.cfg.Store(cfg)
	c.onReady()
	c.connMux.Unlock()

	if err := c.saveSession(cfg, s); err != nil {
		return errors.Wrap(err, "save")
	}

	return nil
}
