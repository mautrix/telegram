from html import escape
import struct
import re


# Unicode surrogate handling from
# https://github.com/LonamiWebs/Telethon/blob/master/telethon/extensions/markdown.py
def add_surrogates(text):
    if text is None:
        return None
    return "".join("".join(chr(y) for y in struct.unpack("<HH", x.encode("utf-16-le")))
                   if (0x10000 <= ord(x) <= 0x10FFFF) else x for x in text)


def remove_surrogates(text):
    if text is None:
        return None
    return text.encode("utf-16", "surrogatepass").decode("utf-16")


def trim_reply_fallback_text(text):
    if not text.startswith("> ") or "\n" not in text:
        return text
    lines = text.split("\n")
    while len(lines) > 0 and lines[0].startswith("> "):
        lines.pop(0)
    return "\n".join(lines)


HTML_REPLY_FALLBACK_REGEX = re.compile(r"^<blockquote data-mx-reply>[\s\S]+?</blockquote>")


def trim_reply_fallback_html(html):
    return HTML_REPLY_FALLBACK_REGEX.sub("", html)


def unicode_to_html(text, html, ctrl, tag):
    if "\u0336" not in text and "\u0332" not in text:
        return html
    if not html:
        html = escape(text)
    tag_start = f"<{tag}>"
    tag_end = f"</{tag}>"
    characters = html.split(ctrl)
    html = ""
    in_del = False
    for char in characters:
        if not in_del:
            if len(char) > 1:
                html += char[0:-1]
                char = char[-1]
            html += tag_start
            in_del = True
            html += char
        else:
            if len(char) > 1:
                html += tag_end
                in_del = False
            html += char
    return html


def html_to_unicode(text, ctrl):
    return ctrl.join(text) + ctrl
