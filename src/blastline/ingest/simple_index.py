"""HTML parser for the real PyPI Simple API index."""

from __future__ import annotations

from html.parser import HTMLParser


class SimpleIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.names: set[str] = set()
        self._in_anchor = False
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._in_anchor = True
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._in_anchor:
            return
        candidate = "".join(self._text).strip()
        if candidate:
            self.names.add(candidate)
        self._in_anchor = False
        self._text = []


def parse_simple_index(body: bytes) -> tuple[str, ...]:
    parser = SimpleIndexParser()
    parser.feed(body.decode("utf-8"))
    parser.close()
    if not parser.names:
        raise ValueError("PyPI Simple API returned no package names")
    return tuple(sorted(parser.names))
