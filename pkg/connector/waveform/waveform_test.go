package waveform_test

import (
	"testing"

	"github.com/stretchr/testify/assert"

	"go.mau.fi/mautrix-telegram/pkg/connector/waveform"
)

func TestEncode(t *testing.T) {
	assert.Equal(t, []byte{0x01}, waveform.Encode([]int{1}))
	assert.Equal(t, []byte{0xff, 0x03}, waveform.Encode([]int{31, 31}))
	assert.Equal(t, []byte{0x41, 0x0c, 0x52, 0xcc, 0x41}, waveform.Encode([]int{1, 2, 3, 4, 5, 6, 7, 8}))
	assert.Equal(t, []byte{0xff, 0xff, 0xff, 0xff, 0xff}, waveform.Encode([]int{31, 31, 31, 31, 31, 31, 31, 31}))
}

func TestDecode(t *testing.T) {
	// assert.Equal(t, []int{0x01}, waveform.Decode([]byte{1}))
	// assert.Equal(t, []int{0x01, 0x10, 0x00}, waveform.Decode([]byte{1, 2}))
	// assert.Equal(t, []int{0x01, 0x10, 0x00, 0x06, 0x00, 0x02, 0x14, 0x00}, waveform.Decode([]byte{1, 2, 3, 4, 5}))
}

func FuzzRoundtrip(f *testing.F) {
	f.Add([]byte{0x01})

	f.Fuzz(func(t *testing.T, w []byte) {
		wf := make([]int, len(w))
		for i, v := range waveform.NormalizeWaveform(wf) {
			wf[i] = int(v)
		}
		encoded := waveform.Encode(wf)
		decoded := waveform.Decode(encoded)

		// Sometimes, the decoded wavefeorm might have an extra value if the
		// last value of the encoded waveform is packed into the 3
		// least-significant bits of the last byte. In that case, it's unclear
		// whether the waveform contains a 0b00000 as the last byte or if there
		// shouldn't have been anything there.
		if len(wf) != len(decoded) {
			assert.Len(t, decoded, len(wf)+1)
			wf = append(wf, 0x00)
		}
		assert.Equal(t, wf, decoded)
	})
}
