# Unicode surrogate handling
# From https://github.com/LonamiWebs/Telethon/blob/master/telethon/extensions/markdown.py
import struct


def add_surrogates(text):
    if text is None:
        return None
    return "".join("".join(chr(y) for y in struct.unpack("<HH", x.encode("utf-16-le")))
                   if (0x10000 <= ord(x) <= 0x10FFFF) else x for x in text)


def remove_surrogates(text):
    if text is None:
        return None
    return text.encode("utf-16", "surrogatepass").decode("utf-16")
