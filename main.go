package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/gotd/td/session"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/auth"
	"github.com/gotd/td/telegram/updates"
	updhook "github.com/gotd/td/telegram/updates/hook"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/msgconv"
)

type FileSession struct{}

func (s *FileSession) LoadSession(context.Context) ([]byte, error) {
	if data, err := os.ReadFile("session"); err != nil {
		return nil, session.ErrNotFound
	} else {
		return data, nil
	}
}

func (s *FileSession) StoreSession(ctx context.Context, data []byte) error {
	return os.WriteFile("session", data, 0600)
}

type Authenticator struct{}

func (a *Authenticator) Phone(ctx context.Context) (string, error) {
	reader := bufio.NewReader(os.Stdin)
	fmt.Printf("Phone (include country code with +): ")
	raw, err := reader.ReadString('\n')
	return strings.TrimSpace(raw), err
}

func (a *Authenticator) Password(ctx context.Context) (string, error) {
	reader := bufio.NewReader(os.Stdin)
	fmt.Printf("Password: ")
	raw, err := reader.ReadString('\n')
	return strings.TrimSpace(raw), err
}

func (a *Authenticator) AcceptTermsOfService(ctx context.Context, tos tg.HelpTermsOfService) error {
	return nil
}

func (a *Authenticator) SignUp(ctx context.Context) (auth.UserInfo, error) {
	panic("not supported")
}

func (a *Authenticator) Code(ctx context.Context, sentCode *tg.AuthSentCode) (string, error) {
	reader := bufio.NewReader(os.Stdin)
	fmt.Printf("Code: ")
	return reader.ReadString('\n')
}

type FakePortal struct {
}

func (*FakePortal) DownloadMedia(ctx context.Context, uri id.ContentURIString, file *event.EncryptedFileInfo) ([]byte, error) {
	return nil, nil
}

func (*FakePortal) UploadMedia(ctx context.Context, roomID id.RoomID, data []byte, fileName, mimeType string) (url id.ContentURIString, file *event.EncryptedFileInfo, err error) {
	return id.ContentURIString("mxc://test"), nil, nil
}

func main() {
	apiID, err := strconv.ParseInt(os.Args[1], 10, 32)
	if err != nil {
		panic(err)
	}
	apiHash := os.Args[2]

	log.Logger = log.Output(zerolog.ConsoleWriter{Out: os.Stderr})

	zaplog := zap.New(zerozap.New(log.Logger))

	var authenticator Authenticator
	var sessionStorage FileSession

	d := tg.NewUpdateDispatcher()
	gaps := updates.New(updates.Config{
		Handler: d,
		Logger:  zaplog.Named("gaps"),
	})

	// https://core.telegram.org/api/obtaining_api_id
	client := telegram.NewClient(int(apiID), apiHash, telegram.Options{
		SessionStorage: &sessionStorage,
		Logger:         zaplog,
		UpdateHandler:  gaps,
		Middlewares: []telegram.Middleware{
			updhook.UpdateHook(gaps.Handle),
		},
	})

	portal := &FakePortal{}
	mc := msgconv.MessageConverter{
		PortalMethods: portal,
		Client:        client,
	}

	// Setup message update handlers.
	d.OnNewChannelMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateNewChannelMessage) error {
		log.Info().Any("update", update.Message).Msg("Channel message")
		converted := mc.ToMatrix(ctx, update.Message)
		fmt.Printf("CONVERTED\n")
		fmt.Printf("CONVERTED\n")
		for _, part := range converted.Parts {
			wrapped := &event.Content{Parsed: part.Content, Raw: part.Extra}
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			enc.Encode(wrapped)
		}
		fmt.Printf("CONVERTED\n")
		fmt.Printf("CONVERTED\n")
		return nil
	})
	d.OnNewMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateNewMessage) error {
		log.Info().Any("update", update.Message).Msg("Message")
		return nil
	})
	d.OnEditChannelMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateEditChannelMessage) error {
		fmt.Printf("on edit channel message %v\n", update)
		return nil
	})
	d.OnEditMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateEditMessage) error {
		fmt.Printf("on edit message %v\n", update)
		return nil
	})

	if err := client.Run(context.Background(), func(ctx context.Context) error {
		authFlow := auth.NewFlow(&authenticator, auth.SendCodeOptions{})
		err := client.Auth().IfNecessary(ctx, authFlow)
		if err != nil {
			return err
		}

		user, err := client.Self(ctx)
		if err != nil {
			return fmt.Errorf("error getting self: %w", err)
		}

		return gaps.Run(ctx, client.API(), user.ID, updates.AuthOptions{
			OnStart: func(ctx context.Context) {
				log.Info().Msg("gaps started")
			},
		})
	}); err != nil {
		panic(err)
	}
	// Client is closed.
}
