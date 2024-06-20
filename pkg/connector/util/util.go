package util

import (
	"fmt"
	"strings"
)

func FormatFullName(first, last string) string {
	return strings.TrimSpace(fmt.Sprintf("%s %s", first, last))
}
