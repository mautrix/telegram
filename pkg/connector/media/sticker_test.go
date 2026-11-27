package media

import "testing"

func TestDisplaySize(t *testing.T) {
	cases := []struct{ fileW, fileH, maxW, maxH, wantW, wantH int }{
		{512, 512, 256, 256, 256, 256},
		{512, 256, 256, 256, 256, 128},
		{200, 100, 256, 256, 200, 100},
	}
	for _, c := range cases {
		if w, h := displaySize(c.fileW, c.fileH, c.maxW, c.maxH); w != c.wantW || h != c.wantH {
			t.Fatalf("displaySize(%d,%d,%d,%d)=%d,%d want %d,%d", c.fileW, c.fileH, c.maxW, c.maxH, w, h, c.wantW, c.wantH)
		}
	}
}
