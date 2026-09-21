"""A strict checker for Telegram's HTML subset, so QA can catch markup that real Telegram would reject.

The fake-network tests cannot notice an unescaped '&' or an unclosed tag -- but Telegram answers those with a 400 "can't parse
entities" and the user simply gets no reply. Rules (https://core.telegram.org/bots/api#html-style):
  * tags: b strong i em u ins s strike del a code pre blockquote tg-spoiler ; every tag closed, in order
  * <a> needs an http(s) / tg href ; <blockquote> takes only the 'expandable' attribute and cannot be nested
  * code / pre cannot contain other tags
  * a literal '<', '>' or '&' in text must be written &lt; &gt; &amp; ; only &lt; &gt; &amp; &quot; and numeric entities exist
"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import List

ALLOWED = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre", "blockquote", "tg-spoiler"}
ENTITIES = {"amp", "lt", "gt", "quot"}


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: List[str] = []
        self.problems: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.problems.append(f"unsupported tag <{tag}>")
        if any(t in self.stack for t in ("code", "pre")):
            self.problems.append(f"<{tag}> inside code/pre")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not href.startswith(("http://", "https://", "tg://")):
                self.problems.append(f"<a> without an http(s)/tg href: {href!r}")
        if tag == "blockquote":
            if any(k != "expandable" for k, _ in attrs):
                self.problems.append(f"<blockquote> with unsupported attributes {attrs}")
            if "blockquote" in self.stack:
                self.problems.append("nested <blockquote>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"unexpected </{tag}> (open: {self.stack})")
        else:
            self.stack.pop()

    def handle_entityref(self, name):
        if name not in ENTITIES:
            self.problems.append(f"unsupported entity &{name};")

    def handle_charref(self, name):
        pass                                                  # numeric entities (&#39; &#x27;) are supported

    def handle_data(self, data):
        for ch, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
            if ch in data:
                self.problems.append(f"raw {ch!r} in text (write {entity}): ...{data[max(0, data.index(ch) - 15):data.index(ch) + 15]!r}")

    def handle_startendtag(self, tag, attrs):
        self.problems.append(f"self-closing <{tag}/> is not supported")


def problems(text: str, limit: int = 4096) -> List[str]:
    """Everything Telegram would object to in `text` (empty list = accepted)."""
    if not text or not text.strip():
        return ["empty message"]
    c = _Checker()
    c.feed(text)
    c.close()
    out = list(c.problems)
    if c.stack:
        out.append(f"unclosed tags: {c.stack}")
    if len(text) > limit:
        out.append(f"{len(text)} chars > {limit}")
    return out
