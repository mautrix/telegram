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

// Package waveform implements encoding and decoding of a Telegram waveform.
//
// Telegram waveforms consist of packed 5-bit values. The values are packed
// into a byte stream, meaning that the actual values cross the byte boundary.
//
// The following diagram explains the format:
//
//	[210|43210][0|43210|43][3210|4321][10|43210|4]...
//	[111|00000][3|22222|11][4444|3333][66|55555|4]...
//
// Explanation of diagram:
//   - The []'s enclose byte boundaries.
//   - The |s represent separation between waveform values.
//   - The numbers in the first row indicate the binary power.
//   - The numbers in the second row indicate the corresponding waveform index.
package waveform

import "math"

// NormalizeWaveform normalizes a waveform by bounding the values to the range
// [0, 32] which is required for the encoding to work.
func NormalizeWaveform(waveform []int) (normalized []byte) {
	normalized = make([]byte, len(waveform))
	var waveformMax int
	for _, v := range waveform {
		waveformMax = max(waveformMax, v)
	}
	for i, v := range waveform {
		normalized[i] = byte(math.Round(float64(v) / float64(max(waveformMax/256, 1))))
	}
	return
}

// Encode normalizes and encodes the input Matrix waveform into a Telegram
// waveform.
func Encode(waveform []int) []byte {
	bytesCount := (len(waveform)*5 + 7) / 8
	result := make([]byte, bytesCount+1)

	var bitShift int
	for i, v := range NormalizeWaveform(waveform) {
		result[i*5/8] |= v << bitShift
		result[i*5/8+1] |= v >> (8 - bitShift)
		bitShift = (bitShift + 5) % 8
	}
	return result[:bytesCount]
}

// Decode decodes a Telegram waveform into a waveform usable by Matrix.
func Decode(waveform []byte) []int {
	numValues := len(waveform) * 8 / 5
	result := make([]int, numValues)

	var bitShift int
	for i := 0; i < numValues; i++ {
		var val byte
		val |= waveform[i*5/8] >> bitShift
		if i*5/8+1 < len(waveform) {
			val |= waveform[i*5/8+1] << (8 - bitShift)
		}
		result[i] = int(val) & 0b00011111
		bitShift = (bitShift + 5) % 8
	}

	return result
}
