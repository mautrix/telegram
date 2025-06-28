package telegram

import (
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/auth"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/auth/qrlogin"
)

// Auth returns auth client.
func (c *Client) Auth() *auth.Client {
	return auth.NewClient(
		c.tg, c.rand, c.appID, c.appHash,
	)
}

// QR returns QR login helper.
func (c *Client) QR() qrlogin.QR {
	return qrlogin.NewQR(
		c.tg,
		c.appID,
		c.appHash,
		qrlogin.Options{Migrate: c.MigrateTo},
	)
}
