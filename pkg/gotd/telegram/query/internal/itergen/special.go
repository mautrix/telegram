package main

import (
	"go/types"
	"sort"
	"strings"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/query/internal/genutil"
)

func (c *collector) unpackClass(
	field Param,
	typeName, trimPrefix string,
) ([]SpecialCaseChain, error) {
	var r []SpecialCaseChain
	if field.Type == "tg."+typeName {
		impls, err := c.ifaces.Implementations(typeName)
		if err != nil {
			return nil, errors.Wrapf(err, "find %q constructors", typeName)
		}
		for _, impl := range impls {
			s, ok := impl.Underlying().(*types.Struct)
			if !ok {
				continue
			}

			cse := SpecialCaseChain{
				ConstructorName: strings.TrimPrefix(impl.Obj().Name(), trimPrefix),
				ConstructorType: genutil.PrintType(impl),
				Field:           field,
			}

			if strings.Contains(cse.ConstructorName, "Empty") {
				continue
			}

			for i := 0; i < s.NumFields(); i++ {
				field := s.Field(i)
				if field.Name() == "Flags" {
					continue
				}

				cse.Args = append(cse.Args, varToParam(field))
			}

			cse.Args = sortParams(cse.Args)
			r = append(r, cse)
		}
	}

	return r, nil
}

func (c *collector) unpackClasses(
	field Param,
	classes ...[2]string,
) ([]SpecialCaseChain, error) {
	var r []SpecialCaseChain
	for _, class := range classes {
		cases, err := c.unpackClass(field, class[0], class[1])
		if err != nil {
			return nil, errors.Wrapf(err, "unpack %q", class[0])
		}
		r = append(r, cases...)
	}

	return r, nil
}

func (c *collector) collectSpecial(m Method) ([]SpecialCaseChain, error) {
	var r []SpecialCaseChain
	for _, field := range m.AdditionalParams {
		cases, err := c.unpackClasses(field, [][2]string{
			{"MessagesFilterClass", "InputMessagesFilter"},
			{"ChannelParticipantsFilterClass", "ChannelParticipants"},
		}...)
		if err != nil {
			return nil, err
		}

		r = append(r, cases...)
	}

	sort.SliceStable(r, func(i, j int) bool {
		return r[i].ConstructorName < r[j].ConstructorName
	})
	return r, nil
}
