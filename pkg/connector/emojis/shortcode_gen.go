//go:build ignore

package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"

	"go.mau.fi/util/exerrors"
	"go.mau.fi/util/variationselector"
)

type Emoji struct {
	Unified   string `json:"unified"`
	ShortName string `json:"short_name"`
}

func unifiedToUnicode(input string) string {
	parts := strings.Split(input, "-")
	output := make([]rune, len(parts))
	for i, part := range parts {
		output[i] = rune(exerrors.Must(strconv.ParseInt(part, 16, 32)))
	}
	return string(output)
}

func main() {
	resp := exerrors.Must(http.Get("https://raw.githubusercontent.com/iamcal/emoji-data/master/emoji.json"))
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		panic(fmt.Errorf("unexpected status code %d", resp.StatusCode))
	}
	var emojis []*Emoji
	exerrors.PanicIfNotNil(json.NewDecoder(resp.Body).Decode(&emojis))
	output := make(map[string]string)
	for _, emoji := range emojis {
		output[variationselector.Add(unifiedToUnicode(emoji.Unified))] = emoji.ShortName
	}
	f := exerrors.Must(os.OpenFile("shortcodes.json", os.O_WRONLY|os.O_TRUNC|os.O_CREATE, 0644))
	exerrors.PanicIfNotNil(json.NewEncoder(f).Encode(output))
	_ = f.Close()
}
