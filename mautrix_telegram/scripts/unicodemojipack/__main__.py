# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2022 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from typing import Any, Literal, TypedDict
from pathlib import Path
import argparse
import asyncio
import io
import json
import logging
import math
import mimetypes
import pickle
import random
import string

from lottie.exporters import export_tgs
from lottie.exporters.cairo import export_png
from lottie.exporters.tgs_validator import Severity, TgsValidator
from lottie.importers.svg import import_svg
from lottie.objects import Animation
from lottie.utils.stripper import float_strip
from PIL import Image
from telethon import TelegramClient
from telethon.custom import Conversation, Message
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import (
    Document,
    DocumentAttributeCustomEmoji,
    DocumentAttributeFilename,
    DocumentAttributeImageSize,
    InputMediaUploadedDocument,
    InputStickerSetShortName,
)
import aiohttp

mimetypes.add_type("image/webp", ".webp")

parser = argparse.ArgumentParser(description="mautrix-telegram unicode emoji packer")
parser.add_argument(
    "-i", "--api-id", type=int, required=True, metavar="<api id>", help="Telegram API ID"
)
parser.add_argument(
    "-a", "--api-hash", type=str, required=True, metavar="<api hash>", help="Telegram API hash"
)
parser.add_argument(
    "-s",
    "--session",
    type=str,
    default="unicodemojipacker.session",
    metavar="<file name>",
    help="Telethon session name",
)
parser.add_argument(
    "-o",
    "--output",
    type=str,
    default="mautrix_telegram/unicodemojipack.json",
    metavar="<file name>",
    help="Path to save created emoji pack document IDs",
)
parser.add_argument(
    "-f",
    "--font-directory",
    type=Path,
    required=True,
    metavar="<directory path>",
    help="Path to the Noto color emoji files",
)
parser.add_argument(
    "-m",
    "--media-directory",
    type=Path,
    required=True,
    metavar="<directory path>",
    help="Path to save converted tgs and webp emoji files",
)
args = parser.parse_args()
font_dir: Path = args.font_directory
media_dir: Path = args.media_directory

EMOJI_DATA_URL = "https://raw.githubusercontent.com/iamcal/emoji-data/master/emoji.json"


def unified_to_unicode(unified: str) -> str:
    return (
        "".join(rf"\U{chunk:0>8}" for chunk in unified.split("-"))
        .encode("ascii")
        .decode("unicode_escape")
    )


def tag_to_str(unified: str) -> str:
    return "".join(chr(int(x.removeprefix("E00"), 16)) for x in unified.split("-"))


EmojiType = Literal["webp", "tgs"]
PackType = Literal["Animated emoji", "Static emoji"]


class Emoji(TypedDict):
    hex: str
    emoji: str
    type: EmojiType
    filename: str


class EmojiData(TypedDict):
    tgs: list[Emoji]
    webp: list[Emoji]


def parse_emoji_data(tone: dict[str, Any], emoji: dict[str, Any]) -> Emoji:
    hex = (tone["non_qualified"] or tone["unified"]).replace("-FE0F", "")
    filename_hex = hex.replace("-", "_").lower()
    filename = f"svg/emoji_u{filename_hex}.svg"
    if emoji["category"] == "Flags" and emoji["subcategory"] in (
        "country-flag",
        "subdivision-flag",
    ):
        filename = f"third_party/region-flags/waved-svg/emoji_u{filename_hex}.svg"

    with (font_dir / filename).open() as f:
        lot: Animation = import_svg(f)
    float_strip(lot)
    lot.tgs_sanitize()

    output = io.BytesIO()
    export_tgs(lot, output)

    validator = TgsValidator()
    validator(lot)
    validator.check_size(len(output.getvalue()))
    errors = [err for err in validator.errors if err.severity != Severity.Note]
    if errors or ("region-flags" in filename and len(output.getvalue()) > 32768):
        lot.scale(100, 100)

        png_out = io.BytesIO()
        export_png(lot, png_out)
        img = Image.open(png_out)
        output = io.BytesIO()
        output.name = "image.webp"
        img.save(output, "webp")

        media_type: EmojiType = "webp"
    else:
        media_type: EmojiType = "tgs"
    path = media_dir / f"{filename_hex}.{media_type}"
    with path.open("wb") as f:
        f.write(output.getvalue())
    print(
        "Converted", filename, "->", path.name, "//" if errors else "", "\n".join(map(str, errors))
    )

    return {
        "hex": hex,
        "emoji": unified_to_unicode(tone["unified"]),
        "type": media_type,
        "filename": path.name,
    }


async def load_emoji_data() -> EmojiData:
    cache_path = media_dir / "conversion-cache.json"
    try:
        with cache_path.open() as f:
            return json.load(f)
    except FileNotFoundError:
        pass
    async with aiohttp.ClientSession() as sess, sess.get(EMOJI_DATA_URL) as resp:
        raw_emoji_data = sorted(
            await resp.json(content_type=None),
            key=lambda dat: dat["sort_order"],
        )
    tgs_emoji = []
    webp_emoji = []
    for emoji in raw_emoji_data:
        for tone in (emoji, *emoji.get("skin_variations", {}).values()):
            parsed_emoji = parse_emoji_data(tone, emoji)
            if parsed_emoji["type"] == "tgs":
                tgs_emoji.append(parsed_emoji)
            else:
                webp_emoji.append(parsed_emoji)
    full_data = {"tgs": tgs_emoji, "webp": webp_emoji}
    with cache_path.open("w") as f:
        json.dump(full_data, f, ensure_ascii=False)
    return full_data


async def create_pack(conv: Conversation, name: str, pack_type: str) -> None:
    await conv.send_message("/newemojipack")
    resp: Message = await conv.get_response()
    assert "A new set of custom emoji" in resp.raw_text
    assert "Please choose the type" in resp.raw_text
    await conv.send_message(pack_type)
    resp = await conv.get_response()
    if pack_type == "Animated emoji":
        assert "When ready to upload, tell me the name of your set." in resp.raw_text
    else:
        assert "Now choose a name for your set." in resp.raw_text
    await conv.send_message(name)
    resp = await conv.get_response()
    if pack_type == "Animated emoji":
        assert "Now send me the first animated emoji" in resp.raw_text
    else:
        assert "Now send me the custom emoji" in resp.raw_text


async def publish_pack(conv: Conversation, shortname: str) -> None:
    await conv.send_message("/publish")

    resp: Message = await conv.get_response()
    assert "You can send me a custom emoji from your emoji set" in resp.raw_text
    await conv.send_message("/skip")

    resp = await conv.get_response()
    assert "Please provide a short name for your emoji set" in resp.raw_text
    await conv.send_message(shortname)

    resp = await conv.get_response()
    assert "I've just published your emoji set" in resp.raw_text


async def send_emoji(
    conv: Conversation, file: bytes | Path | InputMediaUploadedDocument, emoji: str
) -> None:
    await conv.send_file(file)
    resp: Message = await conv.get_response()
    assert "Send me a replacement emoji that corresponds to your custom emoji" in resp.raw_text
    await conv.send_message(emoji)
    resp = await conv.get_response()
    if "Sorry, too many attempts" in resp.raw_text:
        print(resp.raw_text)
        input("Press enter to continue")
        await conv.send_message(emoji)
        resp = await conv.get_response()
    while "Please send an emoji that best describes your custom emoji." in resp.raw_text:
        emoji = input(f"{emoji} was rejected, provide replacement: ")
        await conv.send_message(emoji)
        resp = await conv.get_response()
    assert "Congratulations" in resp.raw_text


class CachedPack(TypedDict):
    name: str
    short_name: str
    part: int
    type: PackType
    published: bool
    collected: bool
    emojis: list[Emoji]


class CachedData(TypedDict):
    packs: list[CachedPack]


def _split_packs_int(
    emoji_list: list[Emoji], pack_type: PackType, current_part: int, total_parts: int
) -> tuple[list[CachedPack], int]:
    packs = []
    current_pack: CachedPack | None = None
    for i, emoji in enumerate(emoji_list):
        if i % 200 == 0:
            current_part += 1
            random_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            short_name = f"mxtg_unicodemoji_{random_id}"
            name = f"mautrix-telegram unicodemoji ({current_part}/{total_parts})"
            current_pack = {
                "type": pack_type,
                "short_name": short_name,
                "part": current_part,
                "name": name,
                "published": False,
                "collected": False,
                "emojis": [],
            }
            packs.append(current_pack)
        current_pack["emojis"].append(emoji)
    return packs, current_part


def split_packs(emoji_data: EmojiData) -> list[CachedPack]:
    total_parts = math.ceil(len(emoji_data["tgs"]) / 200) + math.ceil(
        len(emoji_data["webp"]) / 200
    )
    current_part = 0
    animated_packs, current_part = _split_packs_int(
        emoji_data["tgs"], "Animated emoji", current_part, total_parts
    )
    static_packs, current_part = _split_packs_int(
        emoji_data["webp"], "Static emoji", current_part, total_parts
    )
    return animated_packs + static_packs


async def create_and_fill_pack(
    client: TelegramClient, conv: Conversation, pack: CachedPack
) -> None:
    if pack["short_name"] == "mxtg_unicodemoji_xvzs6743":
        print("Continuing pack", pack["name"])
    else:
        print("Creating pack", pack["name"])
        await create_pack(conv, pack["name"], pack["type"])
    total = len(pack["emojis"])
    for i, emoji in enumerate(pack["emojis"]):
        if pack["short_name"] == "mxtg_unicodemoji_xvzs6743" and i < 87:
            continue
        print(f"Adding emoji {i+1}/{total}", emoji["hex"], emoji["emoji"])
        emoji_file = media_dir / emoji["filename"]
        if emoji["type"] == "webp":
            attrs = [
                DocumentAttributeImageSize(w=100, h=100),
                DocumentAttributeFilename(file_name="image.webp"),
            ]
            with emoji_file.open("rb") as f:
                file_handle = await client.upload_file(f, file_name="emoji.webp")
            emoji_file = InputMediaUploadedDocument(
                file_handle, mime_type="image/webp", attributes=attrs
            )
        await send_emoji(conv, emoji_file, emoji["emoji"])
        await asyncio.sleep(2)
    print("Publishing pack", pack["short_name"])
    await publish_pack(conv, pack["short_name"])


async def main():
    logging.basicConfig(level=logging.INFO)

    emoji_data = await load_emoji_data()

    split_cache = media_dir / "split-cache.json"
    try:
        with split_cache.open() as f:
            packs: list[CachedPack] = json.load(f)
    except FileNotFoundError:
        packs = split_packs(emoji_data)
        with split_cache.open("w") as f:
            json.dump(packs, f)

    doc_id_file = Path(args.output)
    try:
        with doc_id_file.open() as f:
            doc_ids = json.load(f)
    except FileNotFoundError:
        doc_ids = {}

    client = TelegramClient(args.session, args.api_id, args.api_hash, flood_sleep_threshold=3600)
    await client.start()
    async with client.conversation("Stickers", max_messages=20000) as conv:
        for pack in packs:
            if not pack["published"]:
                await create_and_fill_pack(client, conv, pack)
                pack["published"] = True
                with split_cache.open("w") as f:
                    json.dump(packs, f, ensure_ascii=False)
            if not pack["collected"] or True:
                print("Collecting document IDs from pack", pack["short_name"])
                stickers = await client(
                    GetStickerSetRequest(InputStickerSetShortName(pack["short_name"]), 0)
                )
                doc: Document
                for i, doc in enumerate(stickers.documents):
                    attr = next(
                        attr
                        for attr in doc.attributes
                        if isinstance(attr, DocumentAttributeCustomEmoji)
                    )
                    base_emoji = attr.alt.replace("\ufe0f", "")
                    emoji = pack["emojis"][i]["emoji"].replace("\ufe0f", "")
                    doc_ids[emoji] = doc.id
                    print(f"Mapped {emoji} (fallback: {base_emoji}) -> {doc_ids[emoji]}")
                pack["collected"] = True
                with split_cache.open("w") as f:
                    json.dump(packs, f, ensure_ascii=False)
                with doc_id_file.open("w") as f:
                    json.dump(doc_ids, f, ensure_ascii=False)
                print("Pack completed")
                await asyncio.sleep(5)
    with open(args.output.replace(".json", ".pickle"), "wb") as f:
        pickle.dump(doc_ids, f)
    print("Wrote pickle")


asyncio.run(main())
