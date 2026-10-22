// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

package main

import (
	"bytes"
	"encoding/csv"
	"fmt"
	"io"
	"os"
	"regexp"
	"slices"
	"strings"

	"go.mau.fi/util/exerrors"
)

var placeholderRegex = regexp.MustCompile(`\{[a-z_]+\}`)

func main() {
	errorCSV := exerrors.Must(os.Open("gen/errors.csv"))
	reader := csv.NewReader(errorCSV)
	var data bytes.Buffer
	data.WriteString("package humanise\n")
	data.WriteString("import \"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr\"\n")
	data.WriteString("func Error(err error) string {\n")
	data.WriteString("rpcErr, ok := tgerr.As(err)\n")
	data.WriteString("if !ok {\n")
	data.WriteString("return err.Error()\n")
	data.WriteString("} else if rpcErr.Code >= 500 || rpcErr.Code < 0 {\n")
	data.WriteString("return \"Telegram is having internal issues, please try again later.\"\n")
	data.WriteString("}\n")
	data.WriteString("switch {\n")
	for {
		row, err := reader.Read()
		if err != nil {
			if err == io.EOF {
				break
			} else {
				panic(err)
			}
		}

		errType := row[0]
		errString := strings.ReplaceAll(row[2], `\`, `\\`)
		errString = strings.ReplaceAll(errString, `"`, `\"`)
		typeParts := strings.Split(errType, "_")
		hasArgument := slices.Contains(typeParts, "X")
		if hasArgument {
			errType = strings.Join(slices.DeleteFunc(typeParts, func(part string) bool { return part == "X" }), "_")
			errString = placeholderRegex.ReplaceAllString(errString, "%d")
		}

		data.WriteString(`case tgerr.Is(err, "`)
		data.WriteString(errType)
		if hasArgument {
			data.WriteString(`"): return fmt.Sprintf("`)
			data.WriteString(errString)
			data.WriteString(`", rpcErr.Argument)`)
		} else {
			data.WriteString(`"): return "`)
			data.WriteString(errString)
			data.WriteString(`"`)
		}
		data.WriteString("\n")

		fmt.Printf("row %+v\n", row)
	}
	data.WriteString("}\n")
	data.WriteString("return err.Error()")
	data.WriteString("}")

	exerrors.PanicIfNotNil(os.WriteFile("errors.go", data.Bytes(), os.ModePerm))
}
