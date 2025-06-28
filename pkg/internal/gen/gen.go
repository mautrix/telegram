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
	"strings"

	"go.mau.fi/util/exerrors"
)

func main() {
	currentDir := exerrors.Must(os.Getwd())

	errorCSV := exerrors.Must(os.Open(currentDir + "/../../internal/gen/errors.csv"))
	reader := csv.NewReader(errorCSV)
	var data bytes.Buffer
	data.WriteString("package humanise\n")
	data.WriteString("import \"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr\"\n")
	data.WriteString("func Error(err error) string {\n")
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

		data.WriteString(`case tgerr.Is(err, "`)
		data.WriteString(row[0])
		data.WriteString(`"): return "`)
		errString := strings.ReplaceAll(row[2], `\`, `\\`)
		errString = strings.ReplaceAll(errString, `"`, `\"`)
		data.WriteString(errString)
		data.WriteString(`"`)
		data.WriteString("\n")

		fmt.Printf("row %+v\n", row)
	}
	data.WriteString("}\n")
	data.WriteString("return err.Error()")
	data.WriteString("}")

	exerrors.PanicIfNotNil(os.WriteFile(currentDir+"/errors.go", data.Bytes(), os.ModePerm))
}
