package tdesktop_test

import (
	"fmt"
	"os"
	"path/filepath"

	"go.mau.fi/mautrix-telegram/pkg/gotd/session/tdesktop"
)

func ExampleRead() {
	home, err := os.UserHomeDir()
	if err != nil {
		panic(err)
	}

	root := filepath.Join(home, "Downloads", "Telegram", "tdata")
	accounts, err := tdesktop.Read(root, nil)
	if err != nil {
		panic(err)
	}

	for _, account := range accounts {
		auth := account.Authorization
		cfg := account.Config
		fmt.Println(auth.UserID, auth.MainDC, cfg.Environment)
	}
}
