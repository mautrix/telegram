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

package connector

import (
	"bytes"
	"cmp"
	"context"
	"fmt"
	"image"
	_ "image/gif"
	"image/png"
	"net/http"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/util/exmaps"
	"go.mau.fi/util/ffmpeg"
	"go.mau.fi/util/variationselector"
	"golang.org/x/image/draw"
	_ "golang.org/x/image/webp"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/commands"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/format"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/emojis"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/uploader"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

func (tc *TelegramClient) fnListEmojiPacks(ce *commands.Event) {
	resp, err := tc.client.API().MessagesGetAllStickers(ce.Ctx, 0)
	if err != nil {
		ce.Reply("Failed to list image packs: %v", err)
		return
	}
	casted, ok := resp.(*tg.MessagesAllStickers)
	if !ok {
		ce.Reply("Unexpected response type: %T", resp)
		return
	}
	lines := make([]string, len(casted.Sets))
	for i, set := range casted.Sets {
		packType := "stickers"
		if set.Emojis {
			packType = "emojis"
		}
		lines[i] = fmt.Sprintf(
			"* %s (%s, %s)",
			format.EscapeMarkdown(set.Title),
			packType,
			format.SafeMarkdownCode(set.ShortName),
		)
	}
	ce.Reply("Your packs:\n\n%s", strings.Join(lines, "\n"))
}

func (tc *TelegramClient) fnUploadEmojiPack(ce *commands.Event) {
	if len(ce.Args) < 3 || !strings.HasPrefix(ce.Args[1], "!") {
		ce.Reply("Usage: `$cmdprefix emoji-pack upload <telegram shortcode> <room ID> <state key>`")
		return
	}
	dbl := ce.User.DoublePuppet(ce.Ctx)
	if dbl == nil {
		ce.Reply("Double puppeting is required to fetch emoji packs from Matrix")
		return
	}
	mx, ok := dbl.(bridgev2.MatrixAPIWithArbitraryRoomState)
	if !ok {
		ce.Reply("Matrix connector does not implement required interface")
		return
	}
	tgPackShortcode := ce.Args[0]
	roomID := id.RoomID(ce.Args[1])
	packStateKey := strings.Join(ce.Args[2:], " ")
	err := tc.main.Bridge.Bot.EnsureJoined(ce.Ctx, roomID)
	if err != nil {
		ce.Reply("Failed to join room: %v", err)
		return
	}
	evt, err := mx.GetStateEvent(ce.Ctx, roomID, event.Type{Type: "im.ponies.room_emotes", Class: event.StateEventType}, packStateKey)
	if err != nil {
		ce.Reply("Failed to get state event: %v", err)
		return
	}
	pack, ok := evt.Content.Parsed.(*event.ImagePackEventContent)
	if !ok {
		ce.Reply("Unexpected parsed content type %T", evt.Content.Parsed)
		return
	}
	evtID := ce.React("\u23f3\ufe0f")
	defer redactReaction(ce, evtID)
	link, err := tc.synchronizeEmojiPack(ce.Ctx, ce, pack, tgPackShortcode)
	if err != nil {
		ce.Reply("Failed to synchronize emoji pack: %v", err)
		return
	}
	ce.Reply("Successfully synchronized %s", link)
}

func resizeEmoji(src image.Image, size int) *image.RGBA {
	resized := image.NewRGBA(image.Rect(0, 0, size, size))
	bounds := src.Bounds()
	srcW, srcH := bounds.Dx(), bounds.Dy()
	if srcW <= 0 || srcH <= 0 {
		return resized
	}

	dstW, dstH := size, size
	if srcW > srcH {
		dstH = srcH * size / srcW
		if dstH < 1 {
			dstH = 1
		}
	} else if srcH > srcW {
		dstW = srcW * size / srcH
		if dstW < 1 {
			dstW = 1
		}
	}

	left := (size - dstW) / 2
	top := (size - dstH) / 2
	dstRect := image.Rect(left, top, left+dstW, top+dstH)
	draw.BiLinear.Scale(resized, dstRect, src, bounds, draw.Over, nil)
	return resized
}

func resizeSticker(src image.Image, maxSide int) *image.RGBA {
	var dstW, dstH int
	bounds := src.Bounds()
	srcW, srcH := bounds.Dx(), bounds.Dy()
	if srcW == srcH {
		dstW = maxSide
		dstH = maxSide
	} else if srcW > srcH {
		dstW = maxSide
		dstH = srcH * maxSide / srcW
	} else {
		dstH = maxSide
		dstW = srcW * maxSide / srcH
	}
	resized := image.NewRGBA(image.Rect(0, 0, dstW, dstH))
	draw.BiLinear.Scale(resized, resized.Bounds(), src, bounds, draw.Over, nil)
	return resized
}

func reencodeImage(data []byte, resizer func(image.Image, int) *image.RGBA, size int) ([]byte, string, error) {
	decoded, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, "", fmt.Errorf("failed to decode image: %w", err)
	}
	var buf bytes.Buffer
	err = png.Encode(&buf, resizer(decoded, size))
	if err != nil {
		return nil, "", fmt.Errorf("failed to re-encode image: %w", err)
	}
	return buf.Bytes(), "image/png", nil
}

func convertGIFToWebM(ctx context.Context, data []byte, scaleFilter string) ([]byte, string, error) {
	if !ffmpeg.Supported() {
		return nil, "", fmt.Errorf("ffmpeg is not available")
	}
	webmData, err := ffmpeg.ConvertBytes(ctx, data, ".webm", nil, []string{
		"-vf", scaleFilter,
		"-c:v", "libvpx-vp9",
		"-pix_fmt", "yuva420p",
		"-t", "3",
		"-f", "webm",
	}, "image/gif")
	if err != nil {
		return nil, "", fmt.Errorf("failed to convert gif to webm: %w", err)
	}
	return webmData, "video/webm", nil
}

func normalizeImage(ctx context.Context, data []byte, info *event.FileInfo, emoji bool) (convertedData []byte, convertedMime string, err error) {
	if emoji {
		if info.MimeType == "image/gif" {
			return convertGIFToWebM(ctx, data, "fps=fps='min(source_fps,30)',scale=100:100:force_original_aspect_ratio=decrease:flags=lanczos,pad=100:100:(ow-iw)/2:(oh-ih)/2:color=0x00000000")
		}
		if info.Width == 100 && info.Height == 100 {
			return data, info.MimeType, nil
		}
		return reencodeImage(data, resizeEmoji, 100)
	} else {
		if info.Width == 512 || info.Height == 512 {
			return data, info.MimeType, nil
		}
		if info.MimeType == "image/gif" {
			return convertGIFToWebM(ctx, data, "fps=fps='min(source_fps,30)',scale=512:512:force_original_aspect_ratio=decrease:flags=lanczos")
		}
		return reencodeImage(data, resizeSticker, 512)
	}
}

func (tc *TelegramClient) synchronizeEmoji(
	ctx context.Context, shortcode string, img *event.ImagePackImage, emoji bool,
) (*tg.InputStickerSetItem, func(int64) error, error) {
	data, err := tc.main.Bridge.Bot.DownloadMedia(ctx, img.URL, nil)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to download %s (%s): %w", shortcode, img.URL, err)
	}
	if img.Info == nil {
		img.Info = &event.FileInfo{}
	}
	if img.Info.MimeType == "" {
		img.Info.MimeType = http.DetectContentType(data)
	}
	origWidth, origHeight := img.Info.Width, img.Info.Height
	cfg, _, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		return nil, nil, fmt.Errorf("failed to decode image config for %s: %w", shortcode, err)
	}
	img.Info.Width = cfg.Width
	img.Info.Height = cfg.Height
	if origWidth == 0 || origHeight == 0 {
		origWidth, origHeight = cfg.Width, cfg.Height
	}
	data, mime, err := normalizeImage(ctx, data, img.Info, emoji)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to normalize image for %s: %w", shortcode, err)
	}
	up, err := uploader.NewUploader(tc.client.API()).FromBytes(ctx, "", data)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to reupload %s: %w", shortcode, err)
	}
	uploaded, err := tc.client.API().MessagesUploadMedia(ctx, &tg.MessagesUploadMediaRequest{
		Media: &tg.InputMediaUploadedDocument{
			File:      up,
			ForceFile: true,
			MimeType:  mime,
		},
		Peer: &tg.InputPeerSelf{},
	})
	if err != nil {
		return nil, nil, fmt.Errorf("failed to finalize reuploaded media for %s: %w", shortcode, err)
	}
	doc, ok := uploaded.(*tg.MessageMediaDocument)
	if !ok {
		return nil, nil, fmt.Errorf("unexpected uploaded media type %T for %s", uploaded, shortcode)
	}
	fakeDoc, ok := doc.Document.(*tg.Document)
	if !ok {
		return nil, nil, fmt.Errorf("unexpected document type %T for %s", doc.Document, shortcode)
	}
	cacheRealDoc := func(realDocID int64) error {
		if realDocID == 0 {
			return fmt.Errorf("failed to get real document ID for %s/%d", shortcode, fakeDoc.ID)
		}
		err = tc.main.Store.TelegramFile.Insert(ctx, &store.TelegramFile{
			LocationID: store.TelegramFileLocationID(strconv.FormatInt(realDocID, 10)),
			MXC:        img.URL,
			MIMEType:   img.Info.MimeType,
			Size:       len(data),
			Width:      origWidth,
			Height:     origHeight,
			Timestamp:  time.Now(),
		})
		if err != nil {
			return fmt.Errorf("failed to cache mxc for %s/%d: %w", shortcode, realDocID, err)
		}
		return nil
	}
	return &tg.InputStickerSetItem{
		Document: fakeDoc.AsInput(),
		Emoji:    "\u2728\ufe0f",
		Keywords: shortcode,
	}, cacheRealDoc, nil
}

func extractNewDocID(oldSet tg.MessagesStickerSetClass, newSetBox tg.MessagesStickerSetClass) int64 {
	newSet, ok := newSetBox.(*tg.MessagesStickerSet)
	if !ok {
		return 0
	}
	oldDocIDs := make(exmaps.Set[int64])
	if oldSet != nil {
		for _, doc := range oldSet.(*tg.MessagesStickerSet).Documents {
			oldDocIDs.Add(doc.GetID())
		}
	}
	var found int64
	for _, doc := range newSet.Documents {
		if !oldDocIDs.Has(doc.GetID()) {
			if found == 0 {
				found = doc.GetID()
			} else {
				return 0
			}
		}
	}
	return found
}

func (tc *TelegramClient) synchronizeEmojiPack(ctx context.Context, ce *commands.Event, pack *event.ImagePackEventContent, packShortcode string) (string, error) {
	resp, err := tc.client.API().StickersCheckShortName(ctx, packShortcode)
	if err != nil && !tgerr.Is(err, tg.ErrShortNameOccupied) {
		return "", fmt.Errorf("failed to check if shortcode is available: %w", err)
	}
	isEmojiPack := slices.Contains(pack.Metadata.Usage, event.ImagePackUsageEmoji) || len(pack.Metadata.Usage) == 0
	var rawSet tg.MessagesStickerSetClass
	if resp {
		var shortcode string
		var img *event.ImagePackImage
		for shortcode, img = range pack.Images {
			break
		}
		if img == nil {
			return "", fmt.Errorf("pack must contain at least one image")
		}
		item, saveCache, err := tc.synchronizeEmoji(ctx, shortcode, img, isEmojiPack)
		if err != nil {
			return "", fmt.Errorf("failed to synchronize emoji %s: %w", shortcode, err)
		}
		rawSet, err = tc.client.API().StickersCreateStickerSet(ctx, &tg.StickersCreateStickerSetRequest{
			Emojis:    isEmojiPack,
			UserID:    &tg.InputUserSelf{},
			Title:     cmp.Or(pack.Metadata.DisplayName, packShortcode),
			ShortName: packShortcode,
			Stickers:  []tg.InputStickerSetItem{*item},
		})
		if err != nil {
			return "", fmt.Errorf("failed to create pack: %w", err)
		}
		err = saveCache(extractNewDocID(nil, rawSet))
		if err != nil {
			return "", fmt.Errorf("failed to cache document ID for new pack: %w", err)
		}
	} else {
		rawSet, err = tc.client.API().MessagesGetStickerSet(ctx, &tg.MessagesGetStickerSetRequest{
			Stickerset: &tg.InputStickerSetShortName{ShortName: packShortcode},
		})
		if err != nil {
			return "", fmt.Errorf("failed to get pack: %w", err)
		}
	}
	set, ok := rawSet.(*tg.MessagesStickerSet)
	if !ok {
		return "", fmt.Errorf("unexpected set type %T", rawSet)
	}
	if !set.Set.Creator {
		return "", fmt.Errorf("set %s was created by someone else", packShortcode)
	}
	isEmojiPack = set.Set.Emojis
	inputSet := &tg.InputStickerSetID{
		ID:         set.Set.ID,
		AccessHash: set.Set.AccessHash,
	}
	deletedMXCs := make(map[id.ContentURIString]*tg.InputDocument, len(set.Documents))
	existingMXCs := make(exmaps.Set[id.ContentURIString], len(set.Documents))
	for _, doc := range set.Documents {
		file, err := tc.main.Store.TelegramFile.GetByLocationID(ctx, store.TelegramFileLocationID(strconv.FormatInt(doc.GetID(), 10)))
		if err != nil {
			return "", fmt.Errorf("failed to get cached file for doc %d: %w", doc.GetID(), err)
		} else if file != nil {
			deletedMXCs[file.MXC] = doc.(*tg.Document).AsInput()
			existingMXCs.Add(file.MXC)
		}
	}
	for shortcode, img := range pack.Images {
		if existingMXCs.Has(img.URL) {
			delete(deletedMXCs, img.URL)
			continue
		}
		existingMXCs.Add(img.URL)
		item, saveCache, err := tc.synchronizeEmoji(ctx, shortcode, img, isEmojiPack)
		if err != nil {
			ce.Reply("Failed to reupload %s: %v", shortcode, err)
			continue
		}
		rawNewSet, err := tc.client.API().StickersAddStickerToSet(ctx, &tg.StickersAddStickerToSetRequest{
			Stickerset: inputSet,
			Sticker:    *item,
		})
		if err != nil {
			if tgerr.Is(err, tg.ErrStickerpackStickersTooMuch) || tgerr.Is(err, tg.ErrStickersTooMuch) {
				return "", err
			}
			ce.Reply("Failed to add %s/%d to pack: %v", shortcode, item.Document.(*tg.InputDocument).ID, err)
			continue
		}
		err = saveCache(extractNewDocID(rawSet, rawNewSet))
		if err != nil {
			return "", fmt.Errorf("failed to cache document ID for new pack: %w", err)
		}
		rawSet = rawNewSet
	}
	for mxc, inputDoc := range deletedMXCs {
		_, err = tc.client.API().StickersRemoveStickerFromSet(ctx, inputDoc)
		if err != nil {
			return "", fmt.Errorf("failed to remove %s/%d from set: %w", mxc, inputDoc.ID, err)
		}
	}
	linktype := "addstickers"
	if isEmojiPack {
		linktype = "addemoji"
	}
	return fmt.Sprintf("https://t.me/%s/%s", linktype, set.Set.ShortName), nil
}

var addStickersRegex = regexp.MustCompile(`^(?:(?:https?://)?(?:t|telegram)\.(?:me|dog)/(?:addstickers|addemoji)/)?([A-Za-z0-9-_]+)(?:\.json)?$`)
var packShortcodeRegex = regexp.MustCompile(`^[A-Za-z0-9-_]+$`)

func redactReaction(ce *commands.Event, evtID id.EventID) {
	if evtID == "" {
		return
	}
	_, _ = ce.Bot.SendMessage(ce.Ctx, ce.OrigRoomID, event.EventRedaction, &event.Content{
		Parsed: &event.RedactionEventContent{
			Redacts: evtID,
		},
	}, nil)
}

func (tc *TelegramClient) fnDownloadEmojiPack(ce *commands.Event) {
	if len(ce.Args) == 0 {
		ce.Reply("Usage: `$cmdprefix emoji-pack download <pack shortcode or link>`")
		return
	}
	spaceRoom, err := tc.userLogin.GetSpaceRoom(ce.Ctx)
	if err != nil {
		ce.Reply("Failed to get space room: %v", err)
		return
	} else if spaceRoom == "" {
		ce.Reply("Can't bridge image packs if personal filtering spaces are disabled")
		return
	}
	evtID := ce.React("\u23f3\ufe0f")
	defer redactReaction(ce, evtID)
	pack, err := tc.DownloadImagePack(ce.Ctx, ce.Args[0])
	if err != nil {
		ce.Reply("Failed to import pack: %v", err)
		return
	}
	if pack.Shortcode == "" && pack.Content.Metadata.BridgedPack != nil {
		pack.Shortcode = pack.Content.Metadata.BridgedPack.URL
	}
	_, err = tc.main.Bridge.Bot.SendState(ce.Ctx, spaceRoom, event.StateUnstableImagePack, pack.Shortcode, &event.Content{
		Parsed: pack.Content,
		Raw:    pack.Extra,
	}, time.Now())
	if err != nil {
		ce.Reply("Failed to send image pack to space: %v", err)
	} else {
		ce.Reply(
			"Successfully bridged image pack to %s",
			format.MarkdownLink("your personal filtering space",
				spaceRoom.URI(tc.main.Bridge.Matrix.ServerName()).MatrixToURL()))
	}
}

func (tc *TelegramClient) DownloadImagePack(ctx context.Context, url string) (*bridgev2.ImportedImagePack, error) {
	var shortName string
	if match := addStickersRegex.FindStringSubmatch(url); match != nil {
		shortName = match[1]
	} else if packShortcodeRegex.MatchString(url) {
		shortName = url
	} else {
		return nil, fmt.Errorf("invalid pack shortcode or link: %s", url)
	}
	rawSet, err := tc.client.API().MessagesGetStickerSet(ctx, &tg.MessagesGetStickerSetRequest{Stickerset: &tg.InputStickerSetShortName{ShortName: shortName}})
	if err != nil {
		return nil, err
	}
	set, ok := rawSet.(*tg.MessagesStickerSet)
	if !ok {
		return nil, fmt.Errorf("unexpected response type: %T", rawSet)
	}
	tc.addStickerPackToCache(set, true)
	linkType := "addstickers"
	usage := event.ImagePackUsageSticker
	if set.Set.Emojis {
		linkType = "addemoji"
		usage = event.ImagePackUsageEmoji
	}
	packURL := fmt.Sprintf("https://t.me/%s/%s", linkType, set.Set.ShortName)
	pack := &event.ImagePackEventContent{
		Images: make(map[string]*event.ImagePackImage, len(set.Documents)),
		Metadata: event.ImagePackMetadata{
			DisplayName: set.Set.Title,
			AvatarURL:   "",
			Usage:       []event.ImagePackUsage{usage},
			Attribution: fmt.Sprintf("Imported from %s", packURL),
			BridgedPack: &event.BridgedStickerPack{
				Network: StickerSourceID,
				URL:     packURL,
			},
		},
	}
	topLevelExtra := map[string]any{
		"fi.mau.telegram.stickerpack": map[string]any{
			"id":         strconv.FormatInt(set.Set.ID, 10),
			"short_name": set.Set.ShortName,
			"emoji_pack": set.Set.Emojis,
		},
	}
	keywords := make(map[int64][]string)
	emojiLists := make(map[int64][]string)
	for _, kw := range set.Keywords {
		keywords[kw.DocumentID] = kw.Keyword
	}
	for _, emojiPack := range set.Packs {
		emoji := variationselector.Add(emojiPack.Emoticon)
		for _, doc := range emojiPack.Documents {
			emojiLists[doc] = append(emojiLists[doc], emoji)
		}
	}
	for i, rawDoc := range set.Documents {
		// TODO use direct media
		mxc, _, info, err := media.NewTransferer(tc.client.API()).
			WithStickerConfig(tc.main.Config.AnimatedSticker).
			WithForceWebmStickerConvert(set.Set.Emojis).
			WithDocument(rawDoc, false).
			Transfer(ctx, tc.main.Store, tc.main.Bridge.Bot)
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Msg("Failed to transfer image in pack")
			return nil, fmt.Errorf("failed to transfer document %d: %w", rawDoc.GetID(), err)
		}
		kws := keywords[rawDoc.GetID()]
		imageEmojis := emojiLists[rawDoc.GetID()]
		var key string
		for _, kw := range kws {
			_, alreadySet := pack.Images[kw]
			if alreadySet {
				continue
			}
			key = kw
			break
		}
		var firstShortcode string
		if key == "" {
			for _, emoji := range imageEmojis {
				shortcode := emojis.GetShortcode(emoji)
				if shortcode == "" {
					continue
				}
				shortcode = fmt.Sprintf("%s_%s", set.Set.ShortName, shortcode)
				if firstShortcode == "" {
					firstShortcode = shortcode
				}
				_, alreadySet := pack.Images[shortcode]
				if alreadySet {
					continue
				}
				key = shortcode
				break
			}
		}
		if key == "" && firstShortcode != "" {
			for i := 2; i < 10000; i++ {
				kw := fmt.Sprintf("%s%d", firstShortcode, i)
				_, alreadySet := pack.Images[kw]
				if alreadySet {
					continue
				}
				key = kw
			}
		}
		if key == "" {
			key = fmt.Sprintf("%s_img%d", set.Set.ShortName, i+1)
		}
		var emoji string
		if len(imageEmojis) > 0 {
			emoji = imageEmojis[0]
		}
		if !set.Set.Emojis {
			// Stickers need extra info in each sticker so they can be accurately bridged back to Telegram
			// Custom emojis don't have space for such info and can be used with just the document ID
			info.BridgedSticker = &event.BridgedSticker{
				Network: StickerSourceID,
				ID:      strconv.FormatInt(rawDoc.GetID(), 10),
				PackURL: StickerPackURLPrefix + set.Set.ShortName,
				Emoji:   emoji,
			}
		}
		pack.Images[key] = &event.ImagePackImage{
			URL:  mxc,
			Body: cmp.Or(emoji, key),
			Info: info,
		}
	}
	return &bridgev2.ImportedImagePack{
		Content:   pack,
		Extra:     topLevelExtra,
		Shortcode: set.Set.ShortName,
	}, nil
}

const StickerSourceID = "telegram"
const StickerPackURLPrefix = "https://t.me/addstickers/"

func (tc *TelegramClient) stickerSourceFromAttribute(ctx context.Context, documentID int64, attr *tg.DocumentAttributeSticker) *event.BridgedSticker {
	var shortName string
	switch set := attr.Stickerset.(type) {
	case *tg.InputStickerSetID:
		pack, err := tc.GetCachedStickerPack(ctx, "", set, false)
		if err != nil {
			zerolog.Ctx(ctx).Debug().Err(err).
				Int64("pack_id", set.ID).
				Msg("Failed to get sticker pack by ID to fill info")
			return nil
		}
		shortName = pack.meta.ShortName
	case *tg.InputStickerSetShortName:
		shortName = set.ShortName
	default:
		return nil
	}
	return &event.BridgedSticker{
		Network: StickerSourceID,
		ID:      strconv.FormatInt(documentID, 10),
		Emoji:   attr.Alt,
		PackURL: StickerPackURLPrefix + shortName,
	}
}

type stickerPackCache struct {
	docs map[int64]*tg.Document
	meta tg.StickerSet
}

func (tc *TelegramClient) GetCachedStickerPack(ctx context.Context, shortName string, id *tg.InputStickerSetID, forceClearCache bool) (*stickerPackCache, error) {
	tc.stickerPackCacheLock.Lock()
	defer tc.stickerPackCacheLock.Unlock()
	cacheName := strings.ToLower(shortName)
	cache, ok := tc.stickerPacksByName[cacheName]
	if !ok {
		cache, ok = tc.stickerPacksByID[id.GetID()]
	}
	if !ok || forceClearCache {
		var inputSet tg.InputStickerSetClass = id
		if id == nil {
			inputSet = &tg.InputStickerSetShortName{ShortName: shortName}
		}
		resp, err := tc.client.API().MessagesGetStickerSet(ctx, &tg.MessagesGetStickerSetRequest{Stickerset: inputSet})
		if err != nil {
			if tgerr.Is(err, tg.ErrStickersetInvalid) {
				if cacheName != "" {
					tc.stickerPacksByName[cacheName] = nil
				}
				if id != nil {
					tc.stickerPacksByID[id.GetID()] = nil
				}
			}
			return nil, fmt.Errorf("failed to get sticker set: %w", err)
		}
		set, ok := resp.AsModified()
		if !ok {
			if cacheName != "" {
				tc.stickerPacksByName[cacheName] = nil
			}
			if id != nil {
				tc.stickerPacksByID[id.GetID()] = nil
			}
			return nil, fmt.Errorf("unexpected response type for MessagesGetStickerSet: %T", resp)
		}
		cache = tc.addStickerPackToCache(set, false)
	}
	return cache, nil
}

func (tc *TelegramClient) addStickerPackToCache(set *tg.MessagesStickerSet, lock bool) *stickerPackCache {
	if lock {
		tc.stickerPackCacheLock.Lock()
		defer tc.stickerPackCacheLock.Unlock()
	}
	cache := &stickerPackCache{
		docs: set.MapDocuments().DocumentToMap(),
		meta: set.Set,
	}
	tc.stickerPacksByName[strings.ToLower(set.Set.ShortName)] = cache
	tc.stickerPacksByID[set.Set.ID] = cache
	return cache
}

func (tc *TelegramClient) findOriginalStickerDocument(ctx context.Context, meta *event.BridgedSticker, forceClearCache bool) (tg.InputMediaClass, error) {
	if meta == nil || !strings.HasPrefix(meta.PackURL, StickerPackURLPrefix) {
		return nil, nil
	}
	shortName := strings.TrimPrefix(meta.PackURL, StickerPackURLPrefix)
	if shortName == "" {
		return nil, nil
	}
	idNum, err := strconv.ParseInt(meta.ID, 10, 64)
	if err != nil {
		return nil, nil
	}
	cache, err := tc.GetCachedStickerPack(ctx, shortName, nil, forceClearCache)
	if err != nil {
		return nil, err
	}
	stickerDoc, ok := cache.docs[idNum]
	if !ok {
		return nil, nil
	}
	return &tg.InputMediaDocument{ID: stickerDoc.AsInput()}, nil
}
