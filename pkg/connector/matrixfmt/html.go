// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2024 Sumner Evans
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

package matrixfmt

import (
	"context"
	"fmt"
	"math"
	"strconv"
	"strings"

	"golang.org/x/exp/slices"
	"golang.org/x/net/html"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/telegramfmt"
)

type EntityString struct {
	String   telegramfmt.UTF16String
	Entities telegramfmt.BodyRangeList
}

var DebugLog = func(format string, args ...any) {}

func NewEntityString(val string) *EntityString {
	DebugLog("NEW %q\n", val)
	return &EntityString{
		String: telegramfmt.NewUTF16String(val),
	}
}

func (es *EntityString) Split(at uint16) []*EntityString {
	if at > 0x7F {
		panic("cannot split at non-ASCII character")
	}
	if es == nil {
		return []*EntityString{}
	}
	DebugLog("SPLIT %q %q %+v\n", es.String, rune(at), es.Entities)
	var output []*EntityString
	prevSplit := 0
	doSplit := func(i int) *EntityString {
		newES := &EntityString{
			String: es.String[prevSplit:i],
		}
		for _, entity := range es.Entities {
			if (entity.End() <= i || entity.End() > prevSplit) && (entity.Start >= prevSplit || entity.Start < i) {
				entity = *entity.TruncateStart(prevSplit).TruncateEnd(i).Offset(-prevSplit)
				if entity.Length > 0 {
					newES.Entities = append(newES.Entities, entity)
				}
			}
		}
		return newES
	}
	for i, chr := range es.String {
		if chr != at {
			continue
		}
		newES := doSplit(i)
		output = append(output, newES)
		DebugLog("  -> %q %+v\n", newES.String, newES.Entities)
		prevSplit = i + 1
	}
	if prevSplit == 0 {
		DebugLog("  -> NOOP\n")
		return []*EntityString{es}
	}
	if prevSplit != len(es.String) {
		newES := doSplit(len(es.String))
		output = append(output, newES)
		DebugLog("  -> %q %+v\n", newES.String, newES.Entities)
	}
	DebugLog("SPLITEND\n")
	return output
}

func (es *EntityString) TrimSpace() *EntityString {
	if es == nil {
		return nil
	}
	DebugLog("TRIMSPACE %q %+v\n", es.String, es.Entities)
	var cutEnd, cutStart int
	for cutStart = 0; cutStart < len(es.String); cutStart++ {
		switch es.String[cutStart] {
		case '\t', '\n', '\v', '\f', '\r', ' ', 0x85, 0xA0:
			continue
		}
		break
	}
	for cutEnd = len(es.String) - 1; cutEnd >= 0; cutEnd-- {
		switch es.String[cutEnd] {
		case '\t', '\n', '\v', '\f', '\r', ' ', 0x85, 0xA0:
			continue
		}
		break
	}
	cutEnd++
	if cutStart == 0 && cutEnd == len(es.String) {
		DebugLog("  -> NOOP\n")
		return es
	}
	newEntities := es.Entities[:0]
	for _, ent := range es.Entities {
		ent = *ent.Offset(-cutStart).TruncateEnd(cutEnd)
		if ent.Length > 0 {
			newEntities = append(newEntities, ent)
		}
	}
	es.String = es.String[cutStart:cutEnd]
	es.Entities = newEntities
	DebugLog("  -> %q %+v\n", es.String, es.Entities)
	return es
}

func JoinEntityString(with string, strings ...*EntityString) *EntityString {
	withUTF16 := telegramfmt.NewUTF16String(with)
	totalLen := 0
	totalEntities := 0
	for _, s := range strings {
		totalLen += len(s.String)
		totalEntities += len(s.Entities)
	}
	str := make(telegramfmt.UTF16String, 0, totalLen+len(strings)*len(withUTF16))
	entities := make(telegramfmt.BodyRangeList, 0, totalEntities)
	DebugLog("JOIN %q %d\n", with, len(strings))
	for _, s := range strings {
		if s == nil || len(s.String) == 0 {
			continue
		}
		DebugLog("  + %q %+v\n", s.String, s.Entities)
		for _, entity := range s.Entities {
			entity.Start += len(str)
			entities = append(entities, entity)
		}
		str = append(str, s.String...)
		str = append(str, withUTF16...)
	}
	DebugLog("  -> %q %+v\n", str, entities)
	return &EntityString{
		String:   str,
		Entities: entities,
	}
}

func (es *EntityString) Format(value telegramfmt.BodyRangeValue) *EntityString {
	if es == nil {
		return nil
	}
	newEntity := telegramfmt.BodyRange{
		Start:  0,
		Length: len(es.String),
		Value:  value,
	}
	es.Entities = append(telegramfmt.BodyRangeList{newEntity}, es.Entities...)
	DebugLog("FORMAT %v %q %+v\n", value, es.String, es.Entities)
	return es
}

func (es *EntityString) Append(other *EntityString) *EntityString {
	if es == nil {
		return other
	} else if other == nil {
		return es
	}
	DebugLog("APPEND %q %+v\n  + %q %+v\n", es.String, es.Entities, other.String, other.Entities)
	for _, entity := range other.Entities {
		entity.Start += len(es.String)
		es.Entities = append(es.Entities, entity)
	}
	es.String = append(es.String, other.String...)
	DebugLog("  -> %q %+v\n", es.String, es.Entities)
	return es
}

func (es *EntityString) AppendString(other string) *EntityString {
	if es == nil {
		return NewEntityString(other)
	} else if len(other) == 0 {
		return es
	}
	DebugLog("APPENDSTRING %q %+v\n  + %q\n", es.String, es.Entities, other)
	es.String = append(es.String, telegramfmt.NewUTF16String(other)...)
	DebugLog("  -> %q %+v\n", es.String, es.Entities)
	return es
}

type TagStack []string

func (ts TagStack) Index(tag string) int {
	for i := len(ts) - 1; i >= 0; i-- {
		if ts[i] == tag {
			return i
		}
	}
	return -1
}

func (ts TagStack) Has(tag string) bool {
	return ts.Index(tag) >= 0
}

type Context struct {
	Ctx                context.Context
	AllowedMentions    *event.Mentions
	TagStack           TagStack
	PreserveWhitespace bool
}

func NewContext(ctx context.Context) Context {
	return Context{
		Ctx:      ctx,
		TagStack: make(TagStack, 0, 4),
	}
}

func (ctx Context) WithTag(tag string) Context {
	ctx.TagStack = append(ctx.TagStack, tag)
	return ctx
}

func (ctx Context) WithWhitespace() Context {
	ctx.PreserveWhitespace = true
	return ctx
}

// HTMLParser is a somewhat customizable Matrix HTML parser.
type HTMLParser struct {
	GetGhostDetails func(context.Context, id.UserID) (networkid.UserID, string, int64, bool)
}

// TaggedString is a string that also contains a HTML tag.
type TaggedString struct {
	*EntityString
	tag string
}

func (parser *HTMLParser) maybeGetAttribute(node *html.Node, attribute string) (string, bool) {
	for _, attr := range node.Attr {
		if attr.Key == attribute {
			return attr.Val, true
		}
	}
	return "", false
}

func (parser *HTMLParser) getAttribute(node *html.Node, attribute string) string {
	val, _ := parser.maybeGetAttribute(node, attribute)
	return val
}

// Digits counts the number of digits (and the sign, if negative) in an integer.
func Digits(num int) int {
	if num == 0 {
		return 1
	} else if num < 0 {
		return Digits(-num) + 1
	}
	return int(math.Floor(math.Log10(float64(num))) + 1)
}

func (parser *HTMLParser) listToString(node *html.Node, ctx Context) *EntityString {
	ordered := node.Data == "ol"
	taggedChildren := parser.nodeToTaggedStrings(node.FirstChild, ctx)
	counter := 1
	indentLength := 0
	if ordered {
		start := parser.getAttribute(node, "start")
		if len(start) > 0 {
			counter, _ = strconv.Atoi(start)
		}

		longestIndex := (counter - 1) + len(taggedChildren)
		indentLength = Digits(longestIndex)
	}
	indent := strings.Repeat(" ", indentLength+2)
	var children []*EntityString
	for _, child := range taggedChildren {
		if child.tag != "li" {
			continue
		}
		var prefix string
		if ordered {
			indexPadding := indentLength - Digits(counter)
			if indexPadding < 0 {
				// This will happen on negative start indexes where longestIndex is usually wrong, otherwise shouldn't happen
				indexPadding = 0
			}
			prefix = fmt.Sprintf("%d. %s", counter, strings.Repeat(" ", indexPadding))
		} else {
			prefix = "* "
		}
		es := NewEntityString(prefix).Append(child.EntityString)
		counter++
		parts := es.Split('\n')
		for i, part := range parts[1:] {
			parts[i+1] = NewEntityString(indent).Append(part)
		}
		children = append(children, parts...)
	}
	return JoinEntityString("\n", children...)
}

func (parser *HTMLParser) basicFormatToString(node *html.Node, ctx Context) *EntityString {
	str := parser.nodeToTagAwareString(node.FirstChild, ctx)
	switch node.Data {
	case "b", "strong":
		return str.Format(telegramfmt.Style{Type: telegramfmt.StyleBold})
	case "i", "em":
		return str.Format(telegramfmt.Style{Type: telegramfmt.StyleItalic})
	case "s", "del", "strike":
		return str.Format(telegramfmt.Style{Type: telegramfmt.StyleStrikethrough})
	case "u", "ins":
		return str.Format(telegramfmt.Style{Type: telegramfmt.StyleUnderline})
	case "tt", "code":
		return str.Format(telegramfmt.Style{Type: telegramfmt.StyleCode})
	}
	return str
}

func (parser *HTMLParser) spanToString(node *html.Node, ctx Context) *EntityString {
	str := parser.nodeToTagAwareString(node.FirstChild, ctx)
	if node.Data == "span" {
		_, isSpoiler := parser.maybeGetAttribute(node, "data-mx-spoiler")
		if isSpoiler {
			str = str.Format(telegramfmt.Style{Type: telegramfmt.StyleSpoiler})
		}
	}
	return str
}

func (parser *HTMLParser) headerToString(node *html.Node, ctx Context) *EntityString {
	length := int(node.Data[1] - '0')
	prefix := strings.Repeat("#", length) + " "
	return NewEntityString(prefix).Append(parser.nodeToString(node.FirstChild, ctx)).Format(telegramfmt.Style{Type: telegramfmt.StyleBold})
}

func (parser *HTMLParser) linkToString(node *html.Node, ctx Context) *EntityString {
	str := parser.nodeToTagAwareString(node.FirstChild, ctx)
	href := parser.getAttribute(node, "href")
	if len(href) == 0 {
		return str
	}
	ent := NewEntityString(str.String.String())

	parsedMatrix, err := id.ParseMatrixURIOrMatrixToURL(href)
	if err == nil && parsedMatrix != nil && parsedMatrix.Sigil1 == '@' {
		mxid := parsedMatrix.UserID()
		if ctx.AllowedMentions != nil && !slices.Contains(ctx.AllowedMentions.UserIDs, mxid) {
			// Mention not allowed, use name as-is
			return str
		}
		// FIXME this or GetGhostDetails needs to support non-ghost users too
		userID, username, accessHash, ok := parser.GetGhostDetails(ctx.Ctx, mxid)
		if !ok {
			return str
		} else if username == "" {
			return ent.Format(telegramfmt.Mention{UserID: userID, AccessHash: accessHash})
		} else {
			return NewEntityString("@" + username).Format(telegramfmt.Mention{UserID: userID, Username: username})
		}
	}
	if str.String.String() == href {
		return ent.Format(telegramfmt.Style{Type: telegramfmt.StyleURL, URL: href})
	} else {
		return ent.Format(telegramfmt.Style{Type: telegramfmt.StyleTextURL, URL: href})
	}
}

func (parser *HTMLParser) tagToString(node *html.Node, ctx Context) *EntityString {
	ctx = ctx.WithTag(node.Data)
	switch node.Data {
	case "blockquote":
		return parser.
			nodeToTagAwareString(node.FirstChild, ctx).
			Format(telegramfmt.Style{Type: telegramfmt.StyleBlockquote})
	case "ol", "ul":
		return parser.listToString(node, ctx)
	case "h1", "h2", "h3", "h4", "h5", "h6":
		return parser.headerToString(node, ctx)
	case "br":
		return NewEntityString("\n")
	case "b", "strong", "i", "em", "s", "strike", "del", "u", "ins", "tt", "code":
		return parser.basicFormatToString(node, ctx)
	case "span", "font":
		return parser.spanToString(node, ctx)
	case "a":
		return parser.linkToString(node, ctx)
	case "p":
		return parser.nodeToTagAwareString(node.FirstChild, ctx)
	case "hr":
		return NewEntityString("---")
	case "pre":
		var preStr *EntityString
		var language string
		if node.FirstChild != nil && node.FirstChild.Type == html.ElementNode && node.FirstChild.Data == "code" {
			class := parser.getAttribute(node.FirstChild, "class")
			if strings.HasPrefix(class, "language-") {
				language = class[len("language-"):]
			}
			preStr = parser.nodeToString(node.FirstChild.FirstChild, ctx.WithWhitespace())
		} else {
			preStr = parser.nodeToString(node.FirstChild, ctx.WithWhitespace())
		}
		return preStr.Format(telegramfmt.Style{Type: telegramfmt.StylePre, Language: language})
	default:
		return parser.nodeToTagAwareString(node.FirstChild, ctx)
	}
}

func (parser *HTMLParser) singleNodeToString(node *html.Node, ctx Context) TaggedString {
	switch node.Type {
	case html.TextNode:
		if !ctx.PreserveWhitespace {
			node.Data = strings.ReplaceAll(node.Data, "\n", "")
		}
		return TaggedString{NewEntityString(node.Data), "text"}
	case html.ElementNode:
		return TaggedString{parser.tagToString(node, ctx), node.Data}
	case html.DocumentNode:
		return TaggedString{parser.nodeToTagAwareString(node.FirstChild, ctx), "html"}
	default:
		return TaggedString{&EntityString{}, "unknown"}
	}
}

func (parser *HTMLParser) nodeToTaggedStrings(node *html.Node, ctx Context) (strs []TaggedString) {
	for ; node != nil; node = node.NextSibling {
		strs = append(strs, parser.singleNodeToString(node, ctx))
	}
	return
}

var BlockTags = []string{"p", "h1", "h2", "h3", "h4", "h5", "h6", "ol", "ul", "pre", "blockquote", "div", "hr", "table"}

func (parser *HTMLParser) isBlockTag(tag string) bool {
	for _, blockTag := range BlockTags {
		if tag == blockTag {
			return true
		}
	}
	return false
}

func (parser *HTMLParser) nodeToTagAwareString(node *html.Node, ctx Context) *EntityString {
	strs := parser.nodeToTaggedStrings(node, ctx)
	var output *EntityString
	for _, str := range strs {
		tstr := str.EntityString
		if parser.isBlockTag(str.tag) {
			tstr = NewEntityString("\n").Append(tstr).AppendString("\n")
		}
		if output == nil {
			output = tstr
		} else {
			output = output.Append(tstr)
		}
	}
	return output.TrimSpace()
}

func (parser *HTMLParser) nodeToStrings(node *html.Node, ctx Context) (strs []*EntityString) {
	for ; node != nil; node = node.NextSibling {
		strs = append(strs, parser.singleNodeToString(node, ctx).EntityString)
	}
	return
}

func (parser *HTMLParser) nodeToString(node *html.Node, ctx Context) *EntityString {
	return JoinEntityString("", parser.nodeToStrings(node, ctx)...)
}

// Parse converts Matrix HTML into text using the settings in this parser.
func (parser *HTMLParser) Parse(htmlData string, ctx Context) *EntityString {
	node, _ := html.Parse(strings.NewReader(htmlData))
	return parser.nodeToTagAwareString(node, ctx)
}
