package tljson

import (
	"fmt"

	"github.com/gotd/td/tg"
)

func Parse(v tg.JSONValueClass) (out any, err error) {
	switch val := v.(type) {
	case *tg.JSONBool:
		return val.Value, nil
	case *tg.JSONNumber:
		return val.Value, nil
	case *tg.JSONString:
		return val.Value, nil
	case *tg.JSONArray:
		out := make([]any, len(val.Value))
		for i, entry := range val.Value {
			out[i], err = Parse(entry)
			if err != nil {
				return nil, err
			}
		}
		return out, nil
	case *tg.JSONObject:
		out := make(map[string]any, len(val.Value))
		for _, entry := range val.Value {
			out[entry.Key], err = Parse(entry.Value)
			if err != nil {
				return nil, err
			}
		}
		return out, nil
	case *tg.JSONNull:
		return nil, nil
	default:
		return nil, fmt.Errorf("unknown JSON value type %T", v)
	}
}
