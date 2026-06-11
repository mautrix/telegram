// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 Tulir Asokan
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

package telegramfmt

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/util/exhtml"
	"golang.org/x/net/html"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/format"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func ParseRichText(ctx context.Context, msg *tg.RichMessage, params FormatParams) *event.MessageEventContent {
	r := &rtParser{FormatParams: params}
	r.parseBlocks(ctx, msg.Blocks)
	content := format.HTMLToContent(r.buf.String())
	content.Mentions = &r.mentions
	content.BeeperLinkPreviews = []*event.BeeperLinkPreview{}
	return &content
}

type rtParser struct {
	FormatParams
	buf      strings.Builder
	mentions event.Mentions
}

func (r *rtParser) printf(format string, args ...any) {
	if len(args) == 0 {
		r.buf.WriteString(format)
	} else {
		_, _ = fmt.Fprintf(&r.buf, format, args...)
	}
}

func (r *rtParser) printEscaped(s string) {
	_ = exhtml.EscapeWrite(&r.buf, s)
}

func (r *rtParser) writeTagAndAttrs(tag string, selfClosing bool, attrs ...html.Attribute) {
	r.buf.WriteByte('<')
	r.buf.WriteString(tag)
	for _, attr := range attrs {
		r.buf.WriteByte(' ')
		r.buf.WriteString(attr.Key)
		if attr.Val != "" {
			r.buf.WriteString(`="`)
			r.printEscaped(attr.Val)
			r.buf.WriteByte('"')
		}
	}
	r.buf.WriteByte('>')
}

func (r *rtParser) writeOpeningTag(tag string, attrs ...html.Attribute) {
	r.writeTagAndAttrs(tag, false, attrs...)
}

func (r *rtParser) writeWrappedItem(ctx context.Context, tag string, text tg.RichTextClass, attrs ...html.Attribute) {
	r.writeOpeningTag(tag, attrs...)
	r.parseItem(ctx, text)
	r.buf.WriteString("</")
	r.buf.WriteString(tag)
	r.buf.WriteByte('>')
}

func (r *rtParser) parseItem(ctx context.Context, text tg.RichTextClass) {
	switch v := text.(type) {
	case *tg.TextEmpty:
		// no-op I guess?
	case *tg.TextPlain:
		r.printEscaped(v.Text)
	case *tg.TextBold:
		r.writeWrappedItem(ctx, "strong", v.Text)
	case *tg.TextItalic:
		r.writeWrappedItem(ctx, "em", v.Text)
	case *tg.TextUnderline:
		r.writeWrappedItem(ctx, "u", v.Text)
	case *tg.TextStrike:
		r.writeWrappedItem(ctx, "del", v.Text)
	case *tg.TextFixed:
		r.writeWrappedItem(ctx, "code", v.Text)
	case *tg.TextURL:
		r.writeWrappedItem(ctx, "a", v.Text, html.Attribute{Key: "href", Val: v.URL})
	case *tg.TextEmail:
		r.writeWrappedItem(ctx, "a", v.Text, html.Attribute{Key: "href", Val: "mailto:" + v.Email})
	case *tg.TextAutoURL:
		r.writeAutoURL(ctx, v.Text, "")
	case *tg.TextAutoEmail:
		r.writeAutoURL(ctx, v.Text, "mailto:")
	case *tg.TextConcat:
		r.parseItems(ctx, v.Texts)
	case *tg.TextSubscript:
		r.writeWrappedItem(ctx, "sub", v.Text)
	case *tg.TextSuperscript:
		r.writeWrappedItem(ctx, "sup", v.Text)
	case *tg.TextMarked:
		r.writeWrappedItem(ctx, "span", v.Text, html.Attribute{Key: "data-mx-bg-color", Val: highlightBackgroundColor})
	case *tg.TextPhone:
		// tel: URLs aren't in the recommended HTML subset to allow in Matrix, so just highlight with color
		r.writeWrappedItem(ctx, "span", v.Text, html.Attribute{Key: "data-mx-color", Val: hashColor})
	case *tg.TextAutoPhone:
		r.writeWrappedItem(ctx, "span", v.Text, html.Attribute{Key: "data-mx-color", Val: hashColor})
	case *tg.TextAnchor:
		// Ignore the anchor link as those won't work in Matrix anyway
		r.parseItem(ctx, v.Text)
	case *tg.TextMath:
		r.printf(`<span data-mx-maths="%[1]s"><code>%[1]s</code></span>`, html.EscapeString(v.Source))
	case *tg.TextCustomEmoji:
		r.buf.WriteString(emojiInfoToHTML(r.CustomEmojis[ids.MakeEmojiIDFromDocumentID(v.DocumentID)], v.Alt))
	case *tg.TextSpoiler:
		r.writeWrappedItem(ctx, "span", v.Text, html.Attribute{Key: "data-mx-spoiler"})
	case *tg.TextHashtag:
		r.writeWrappedItem(ctx, "span", v.Text, html.Attribute{Key: "data-mx-color", Val: hashColor})
	case *tg.TextBotCommand:
		r.writeWrappedItem(ctx, "span", v.Text, html.Attribute{Key: "data-mx-color", Val: hashColor})
	case *tg.TextCashtag:
		r.writeWrappedItem(ctx, "span", v.Text, html.Attribute{Key: "data-mx-color", Val: hashColor})
	case *tg.TextBankCard:
		r.writeWrappedItem(ctx, "span", v.Text, html.Attribute{Key: "data-mx-color", Val: hashColor})
	case *tg.TextMention:
		plain, ok := v.Text.(*tg.TextPlain)
		if ok {
			// TODO support channel links (link to room if exists)
			userInfo, err := r.GetUserInfoByUsername(ctx, plain.Text)
			if err != nil {
				zerolog.Ctx(ctx).Debug().Err(err).Str("username", plain.Text).Msg("Failed to get user info for mention")
				ok = false
			} else {
				r.mentions.Add(userInfo.MXID)
				r.writeWrappedItem(ctx, "a", plain, html.Attribute{Key: "href", Val: userInfo.MXID.URI().MatrixToURL()})
			}
		}
		if !ok {
			r.parseItem(ctx, text)
		}
	case *tg.TextMentionName:
		ghost, err := r.Bridge.GetGhostByID(ctx, ids.MakeUserID(v.UserID))
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Msg("Failed to get ghost for mention")
			r.parseItem(ctx, v.Text)
		} else {
			r.mentions.Add(ghost.Intent.GetMXID())
			r.writeWrappedItem(ctx, "a", v.Text, html.Attribute{Key: "href", Val: ghost.Intent.GetMXID().URI().MatrixToURL()})
		}
	case *tg.TextDate:
		r.writeWrappedItem(ctx, "time", v.Text, html.Attribute{Key: "datetime", Val: time.Unix(int64(v.Date), 0).Format(time.RFC3339)})
	case *tg.TextImage:
		r.printf("[Unsupported rich text image]")
	default:
		r.printf("Unsupported rich text type: <code>%T</code>", v)
	}
}

func (r *rtParser) writeAutoURL(ctx context.Context, text tg.RichTextClass, proto string) {
	if plain, ok := text.(*tg.TextPlain); ok {
		url := proto + plain.Text
		if proto == "" && !strings.HasPrefix(url, "http://") && !strings.HasPrefix(url, "https://") {
			url = "https://" + url
		}
		r.writeWrappedItem(ctx, "a", plain, html.Attribute{Key: "href", Val: url})
	} else {
		r.parseItem(ctx, text)
	}
}

func (r *rtParser) parseItems(ctx context.Context, items []tg.RichTextClass) {
	for _, item := range items {
		r.parseItem(ctx, item)
	}
}

func (r *rtParser) parseBlocks(ctx context.Context, items []tg.PageBlockClass) {
	for _, item := range items {
		r.parseBlock(ctx, item)
	}
}

func (r *rtParser) parseBlock(ctx context.Context, block tg.PageBlockClass) {
	switch v := block.(type) {
	case *tg.PageBlockUnsupported:
		r.printf("<p>Unsupported block type</p>")
	case *tg.PageBlockAuthorDate:
		r.printf("<p><em>")
		if v.Author != nil {
			r.parseItem(ctx, v.Author)
		}
		if v.PublishedDate != 0 {
			if v.Author != nil {
				r.printf(", ")
			}
			publishTime := time.Unix(int64(v.PublishedDate), 0)
			r.printf("<time datetime=\"%s\">%s</time>",
				publishTime.Format(time.RFC3339),
				publishTime.Format("2006-01-02"))
		}
		r.printf("</em></p>")
	case *tg.PageBlockPreformatted:
		r.buf.WriteString("<pre>")
		var attrs []html.Attribute
		if v.Language != "" {
			attrs = []html.Attribute{{Key: "class", Val: "language-" + v.Language}}
		}
		r.writeWrappedItem(ctx, "code", v.Text, attrs...)
		r.buf.WriteString("</pre>")
	case *tg.PageBlockFooter:
		r.writeWrappedItem(ctx, "footer", v.Text)
	case *tg.PageBlockDivider:
		r.buf.WriteString("<hr/>")
	case *tg.PageBlockAnchor:
		// Ignore the anchor link as those won't work in Matrix anyway
		r.printEscaped(v.Name)
	case *tg.PageBlockList:
		r.parseList(ctx, v)
	case *tg.PageBlockTable:
		r.parseTable(ctx, v)
	case *tg.PageBlockOrderedList:
		r.parseOrderedList(ctx, v)
	case *tg.PageBlockBlockquote:
		r.writeWrappedItem(ctx, "blockquote", v.Text)
		if v.Caption != nil {
			r.writeWrappedItem(ctx, "p", v.Caption)
		}
	case *tg.PageBlockPullquote:
		r.writeWrappedItem(ctx, "blockquote", v.Text, html.Attribute{Key: "data-tg-pullquote"})
		if v.Caption != nil {
			r.writeWrappedItem(ctx, "p", v.Caption)
		}
	case *tg.PageBlockParagraph:
		r.writeWrappedItem(ctx, "p", v.Text)
	case *tg.PageBlockKicker:
		r.writeWrappedItem(ctx, "p", v.Text, html.Attribute{Key: "data-tg-kicker"})
	case *tg.PageBlockThinking:
		r.writeWrappedItem(ctx, "p", v.Text, html.Attribute{Key: "data-tg-thinking"})
	case *tg.PageBlockChannel:
		r.writeRichTextChannel(ctx, v.Channel)
	case *tg.PageBlockDetails:
		if v.Open {
			r.buf.WriteString("<details open>")
		} else {
			r.buf.WriteString("<details>")
		}
		r.writeWrappedItem(ctx, "summary", v.Title)
		r.parseBlocks(ctx, v.Blocks)
		r.buf.WriteString("</details>")
	case *tg.PageBlockTitle:
		r.writeWrappedItem(ctx, "h1", v.Text)
	case *tg.PageBlockSubtitle:
		r.writeWrappedItem(ctx, "h2", v.Text)
	case *tg.PageBlockHeader:
		r.writeWrappedItem(ctx, "h3", v.Text)
	case *tg.PageBlockSubheader:
		r.writeWrappedItem(ctx, "h4", v.Text)
	case *tg.PageBlockHeading1:
		r.writeWrappedItem(ctx, "h1", v.Text)
	case *tg.PageBlockHeading2:
		r.writeWrappedItem(ctx, "h2", v.Text)
	case *tg.PageBlockHeading3:
		r.writeWrappedItem(ctx, "h3", v.Text)
	case *tg.PageBlockHeading4:
		r.writeWrappedItem(ctx, "h4", v.Text)
	case *tg.PageBlockHeading5:
		r.writeWrappedItem(ctx, "h5", v.Text)
	case *tg.PageBlockHeading6:
		r.writeWrappedItem(ctx, "h6", v.Text)
	case *tg.PageBlockMath:
		r.printf(`<div data-mx-maths="%[1]s"><code>%[1]s</code></div>`, html.EscapeString(v.Source))
	//case *tg.PageBlockPhoto:
	//case *tg.PageBlockVideo:
	//case *tg.PageBlockAudio:
	//case *tg.PageBlockCover:
	//case *tg.PageBlockRelatedArticles:
	//case *tg.PageBlockEmbed:
	//case *tg.PageBlockEmbedPost:
	//case *tg.PageBlockCollage:
	//case *tg.PageBlockSlideshow:
	//case *tg.PageBlockMap:
	//case *tg.InputPageBlockMap:
	//case *tg.PageBlockBlockquoteBlocks:
	default:
		r.printf("<p>Unsupported block type: <code>%T</code></p>", v)
	}
}

type checkboxable interface {
	GetCheckbox() bool
	GetChecked() bool
}

func (r *rtParser) writeCheckbox(item checkboxable) {
	if !item.GetCheckbox() {
		return
	}
	attrs := []html.Attribute{{Key: "type", Val: "checkbox"}}
	if item.GetChecked() {
		attrs = append(attrs, html.Attribute{Key: "checked"})
	}
	r.writeTagAndAttrs("input", true, attrs...)
}

func (r *rtParser) parseList(ctx context.Context, v *tg.PageBlockList) {
	r.buf.WriteString("<ul>")
	for _, item := range v.Items {
		r.buf.WriteString("<li>")
		r.writeCheckbox(item)
		switch vi := item.(type) {
		case *tg.PageListItemText:
			r.parseItem(ctx, vi.Text)
		case *tg.PageListItemBlocks:
			r.parseBlocks(ctx, vi.Blocks)
		default:
			r.printf("Unsupported list item type: <code>%T</code>", item)
		}
		r.buf.WriteString("</li>")
	}
	r.buf.WriteString("</ul>")
}

func (r *rtParser) parseOrderedList(ctx context.Context, v *tg.PageBlockOrderedList) {
	var openAttrs []html.Attribute
	// TODO type?
	if v.Start != 1 {
		openAttrs = append(openAttrs, html.Attribute{Key: "start", Val: fmt.Sprint(v.Start)})
	}
	r.writeOpeningTag("ol", openAttrs...)
	for _, item := range v.Items {
		// TODO jumping indexes
		r.buf.WriteString("<li>")
		r.writeCheckbox(item)
		switch vi := item.(type) {
		case *tg.PageListOrderedItemText:
			r.parseItem(ctx, vi.Text)
		case *tg.PageListOrderedItemBlocks:
			r.parseBlocks(ctx, vi.Blocks)
		default:
			r.printf("Unsupported ordered list item type: <code>%T</code>", item)
		}
		r.buf.WriteString("</li>")
	}
	r.buf.WriteString("</ol>")
}

func (r *rtParser) parseTable(ctx context.Context, v *tg.PageBlockTable) {
	if v.Title != nil {
		r.writeWrappedItem(ctx, "p", v.Title)
	}
	r.buf.WriteString("<table>")
	for _, row := range v.Rows {
		r.buf.WriteString("<tr>")
		for _, cell := range row.Cells {
			var attrs []html.Attribute
			if cell.Colspan > 1 {
				attrs = append(attrs, html.Attribute{Key: "colspan", Val: fmt.Sprint(cell.Colspan)})
			}
			if cell.Rowspan > 1 {
				attrs = append(attrs, html.Attribute{Key: "rowspan", Val: fmt.Sprint(cell.Rowspan)})
			}
			if cell.AlignRight {
				attrs = append(attrs, html.Attribute{Key: "align", Val: "right"})
			} else if cell.AlignCenter {
				attrs = append(attrs, html.Attribute{Key: "align", Val: "center"})
			}
			if cell.ValignMiddle {
				attrs = append(attrs, html.Attribute{Key: "valign", Val: "middle"})
			} else if cell.ValignBottom {
				attrs = append(attrs, html.Attribute{Key: "valign", Val: "bottom"})
			}
			cellType := "td"
			if cell.Header {
				cellType = "th"
			}
			r.writeWrappedItem(ctx, cellType, cell.Text, attrs...)
		}
		r.buf.WriteString("</tr>")
	}
	r.buf.WriteString("</table>")
}

func (r *rtParser) writeRichTextChannel(ctx context.Context, v tg.ChatClass) {
	var portalKey networkid.PortalKey
	var name, url string
	switch ch := v.(type) {
	case *tg.Chat:
		portalKey = r.MakePortalKeyFromID(ids.PeerTypeChat, ch.ID, 0)
		name = ch.Title
		url = fmt.Sprintf("https://t.me/c/%d", ch.ID)
	case *tg.Channel:
		portalKey = r.MakePortalKeyFromID(ids.PeerTypeChannel, ch.ID, 0)
		name = ch.Title
		if ch.Username != "" {
			url = "https://t.me/" + ch.Username
		} else {
			url = fmt.Sprintf("https://t.me/c/%d", ch.ID)
		}
	case *tg.ChatEmpty, *tg.ChatForbidden:
		// no-op
		return
	default:
		r.printf("Unsupported channel type: <code>%T</code>", v)
		return
	}
	portal, err := r.Bridge.GetPortalByKey(ctx, portalKey)
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Msg("Failed to get portal for channel mention")
	} else if portal != nil {
		url = portal.MXID.URI(r.Bridge.Matrix.ServerName()).MatrixToURL()
	}
	r.printf(`<a href="%s">%s</a>`, url, html.EscapeString(name))
}

func FindRichTextCustomEmojis(blocks []tg.PageBlockClass) (emojiDocuments []int64) {
	findRichTextCustomEmojisInBlocks(&emojiDocuments, blocks)
	return
}

type textable interface {
	GetText() tg.RichTextClass
}

type textsable interface {
	GetTexts() []tg.RichTextClass
}

func findRichTextCustomEmojisInText(out *[]int64, texts ...tg.RichTextClass) {
	for _, text := range texts {
		switch typed := text.(type) {
		case *tg.TextCustomEmoji:
			*out = append(*out, typed.DocumentID)
		case textable:
			findRichTextCustomEmojisInText(out, typed.GetText())
		case textsable:
			findRichTextCustomEmojisInText(out, typed.GetTexts()...)
		}
	}
}

type captionable interface {
	GetCaption() tg.RichTextClass
}

type authorable interface {
	GetAuthor() tg.RichTextClass
}

type titleable interface {
	GetTitle() tg.RichTextClass
}

type blocksable interface {
	GetBlocks() []tg.PageBlockClass
}

func findRichTextCustomEmojisInBlocks(out *[]int64, blocks []tg.PageBlockClass) {
	for _, block := range blocks {
		if t, ok := block.(textable); ok {
			findRichTextCustomEmojisInText(out, t.GetText())
		}
		if t, ok := block.(textsable); ok {
			findRichTextCustomEmojisInText(out, t.GetTexts()...)
		}
		if t, ok := block.(captionable); ok {
			findRichTextCustomEmojisInText(out, t.GetCaption())
		}
		if t, ok := block.(authorable); ok {
			findRichTextCustomEmojisInText(out, t.GetAuthor())
		}
		if t, ok := block.(titleable); ok {
			findRichTextCustomEmojisInText(out, t.GetTitle())
		}
		if t, ok := block.(blocksable); ok {
			findRichTextCustomEmojisInBlocks(out, t.GetBlocks())
		}
		switch typedBlock := block.(type) {
		case *tg.PageBlockList:
			for _, item := range typedBlock.Items {
				if t, ok := item.(textable); ok {
					findRichTextCustomEmojisInText(out, t.GetText())
				}
				if t, ok := item.(blocksable); ok {
					findRichTextCustomEmojisInBlocks(out, t.GetBlocks())
				}
			}
		case *tg.PageBlockOrderedList:
			for _, item := range typedBlock.Items {
				if t, ok := item.(textable); ok {
					findRichTextCustomEmojisInText(out, t.GetText())
				}
				if t, ok := item.(blocksable); ok {
					findRichTextCustomEmojisInBlocks(out, t.GetBlocks())
				}
			}
		case *tg.PageBlockTable:
			for _, row := range typedBlock.Rows {
				for _, cell := range row.Cells {
					findRichTextCustomEmojisInText(out, cell.Text)
				}
			}
		}
	}
}
