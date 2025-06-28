package telegram

import (
	"context"

	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mtproto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/pool"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/dcs"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/internal/manager"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/transport"
)

type clientHandler struct {
	client *Client
}

func (c clientHandler) OnSession(cfg tg.Config, s mtproto.Session) (err error) {
	err = c.client.onSession(cfg, s)
	if c.client.opts.Handler != nil {
		c.client.opts.Handler.OnSession(s)
	}
	return
}

func (c clientHandler) OnMessage(b *bin.Buffer) error {
	return c.client.handleUpdates(b)
}

func (c *Client) asHandler() manager.Handler {
	return clientHandler{
		client: c,
	}
}

type connConstructor func(
	create mtproto.Dialer,
	mode manager.ConnMode,
	appID int,
	opts mtproto.Options,
	connOpts manager.ConnOptions,
) pool.Conn

func defaultConstructor() connConstructor {
	return func(
		create mtproto.Dialer,
		mode manager.ConnMode,
		appID int,
		opts mtproto.Options,
		connOpts manager.ConnOptions,
	) pool.Conn {
		return manager.CreateConn(create, mode, appID, opts, connOpts)
	}
}

func (c *Client) dcList() dcs.List {
	cfg := c.cfg.Load()
	return dcs.List{
		Options: cfg.DCOptions,
		Domains: c.domains,
		Test:    c.testDC,
	}
}

func (c *Client) primaryDC(dc int) mtproto.Dialer {
	return func(ctx context.Context) (transport.Conn, error) {
		return c.resolver.Primary(ctx, dc, c.dcList())
	}
}

func (c *Client) createPrimaryConn(setup manager.SetupCallback) pool.Conn {
	return c.createConn(0, c.defaultMode, setup, c.onDead, c.onAuthError)
}

func (c *Client) createConn(
	id int64,
	mode manager.ConnMode,
	setup manager.SetupCallback,
	onDead func(),
	onAuthError func(error),
) pool.Conn {
	opts, s := c.session.Options(c.opts)
	opts.Logger = c.log.Named("conn").With(
		zap.Int64("conn_id", id),
		zap.Int("dc_id", s.DC),
	)

	return c.create(
		c.primaryDC(s.DC), mode, c.appID,
		opts, manager.ConnOptions{
			DC:          s.DC,
			Test:        c.testDC,
			Device:      c.device,
			Handler:     c.asHandler(),
			Setup:       setup,
			OnDead:      onDead,
			OnAuthError: onAuthError,
		},
	)
}
