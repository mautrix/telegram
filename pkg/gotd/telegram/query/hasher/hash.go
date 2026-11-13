// Package hasher contains Telegram pagination hash implementation.
package hasher

// Hasher implements Telegram pagination hash counting.
//
// See https://core.telegram.org/api/offsets#hash-generation.
type Hasher struct {
	state uint64
}

// Reset resets the Hasher to its initial state.
func (h *Hasher) Reset() {
	h.state = 0
}

// Update performs state change using given value.
func (h *Hasher) Update(value uint64) {
	h.state ^= h.state >> 21
	h.state ^= h.state << 35
	h.state ^= h.state >> 4
	h.state += value
}

// Sum returns final sum.
func (h *Hasher) Sum() int64 {
	return int64(h.state)
}
