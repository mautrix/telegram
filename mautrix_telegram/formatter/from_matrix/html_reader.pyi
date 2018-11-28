from typing import Dict, List


class HTMLNode(List['HTMLNode']):
    tag: str
    text: str
    tail: str
    attrib: Dict[str, str]


def read_html(data: str) -> HTMLNode: ...
