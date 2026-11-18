package hasher

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestHasher(t *testing.T) {
	hasher := Hasher{}
	data := []int{7, 5, 16, 8}

	for i := range data {
		hasher.Update(uint64(data[i]))
	}

	require.Equal(t, int64(5165336670474863397), hasher.Sum())
}
