package mtproxy

import (
	"encoding/base64"
	"encoding/hex"
	"strings"
	"unicode"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/proto/codec"
)

// SecretType represents MTProxy secret type.
type SecretType int

const (
	// Simple is a basic MTProxy secret.
	Simple SecretType = iota + 1
	// Secured is dd-secret.
	Secured
	// TLS is fakeTLS MTProxy secret.
	// First byte should be ee.
	TLS
)

// Secret represents MTProxy secret.
type Secret struct {
	Secret    []byte
	Tag       byte
	CloakHost string
	Type      SecretType
}

// ExpectedCodec returns codec from secret tag if it exists.
func (s Secret) ExpectedCodec() (cdc codec.Codec, _ bool) {
	switch s.Tag {
	case codec.AbridgedClientStart[0]:
		cdc = codec.Abridged{}
	case codec.IntermediateClientStart[0]:
		cdc = codec.Intermediate{}
	case codec.PaddedIntermediateClientStart[0]:
		cdc = codec.PaddedIntermediate{}
	default:
		return nil, false
	}

	return cdc, true
}

// ParseSecretString parses a secret from a string, auto-detecting the encoding format.
// Supported formats:
//   - Hex-encoded (e.g. "ee852380f362a09343efb4690c4e17862e676f6f676c652e636f6d")
//   - Base64-encoded (e.g. "eehSO..." with URL-safe or standard base64)
//   - Raw bytes (16-34 bytes)
func ParseSecretString(secret string) ([]byte, error) {
	secret = strings.TrimSpace(secret)
	if secret == "" {
		return nil, errors.New("empty secret")
	}

	if decoded, err := hex.DecodeString(secret); err == nil {
		return decoded, nil
	}

	for _, enc := range []*base64.Encoding{
		base64.RawURLEncoding,
		base64.RawStdEncoding,
		base64.URLEncoding,
		base64.StdEncoding,
	} {
		if decoded, err := enc.DecodeString(secret); err == nil {
			return decoded, nil
		}
	}

	if isAllHex(secret) {
		return nil, errors.Errorf("secret looks like hex but failed to decode: %q", secret)
	}

	return []byte(secret), nil
}

func isAllHex(s string) bool {
	for _, c := range s {
		if !unicode.Is(unicode.ASCII_Hex_Digit, c) {
			return false
		}
	}
	return len(s)%2 == 0
}

// ParseSecret checks and parses secret.
func ParseSecret(secret []byte) (Secret, error) {
	r := Secret{
		Secret: secret,
	}
	const simpleLength = 16

	switch {
	case len(secret) == 1+simpleLength:
		r.Type = Secured

		r.Tag = secret[0]
		secret = secret[1:]
		r.Secret = secret[:simpleLength]
	case len(secret) > simpleLength:
		r.Type = TLS

		r.Tag = secret[0]
		secret = secret[1:]
		r.Secret = secret[:simpleLength]
		r.CloakHost = string(secret[simpleLength:])
	case len(secret) == simpleLength:
		r.Type = Simple
	default:
		return Secret{}, errors.Errorf("invalid secret %q", string(secret))
	}

	if r.Type != Simple {
		switch r.Tag {
		case codec.AbridgedClientStart[0],
			codec.IntermediateClientStart[0],
			codec.PaddedIntermediateClientStart[0]:
		default:
			return Secret{}, errors.Errorf("unknown tag %+x", r.Tag)
		}
	}

	return r, nil
}
