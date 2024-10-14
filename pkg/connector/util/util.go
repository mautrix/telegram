package util

import (
	"fmt"
	"strings"
)

func FormatFullName(first, last string, deleted bool, userID int64) string {
	if deleted {
		return fmt.Sprintf("Deleted account %d", userID)
	}
	return strings.TrimSpace(fmt.Sprintf("%s %s", first, last))
}
