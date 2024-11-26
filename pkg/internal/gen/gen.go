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
	data.WriteString("import \"github.com/gotd/td/tgerr\"\n")
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
