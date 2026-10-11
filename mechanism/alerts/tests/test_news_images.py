"""Thumbnail links for stored headlines (migration 33): the smallest http(s) image Alpaca sends, or none. Needs no chart or
bot dependencies, only news_service itself."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from alerts import news_service as ns  # noqa: E402


def _item(images=None):
    d = {"headline": "Headline", "created_at": "2026-10-09T17:05:00Z", "source": "benzinga",
         "url": "https://news.test/1", "symbols": ["FSLY"]}
    if images is not None:
        d["images"] = images
    return d


def test_picks_the_smallest_size_available():
    imgs = [{"size": "large", "url": "https://img.test/l.jpg"}, {"size": "small", "url": "https://img.test/s.jpg"},
            {"size": "thumb", "url": "https://img.test/t.jpg"}]
    assert ns.pick_image(imgs) == "https://img.test/t.jpg"
    assert ns.pick_image(imgs[:2]) == "https://img.test/s.jpg"


def test_no_images_or_unsafe_links_mean_none():
    assert ns.pick_image(None) is None and ns.pick_image([]) is None
    assert ns.pick_image([{"size": "thumb", "url": "javascript:alert(1)"}, {"size": "small"}]) is None
    assert ns.pick_image([{"size": "thumb", "url": "https://img.test/" + "x" * 1000}]) is None  # too long for the column


def test_unknown_size_label_is_still_used():
    assert ns.pick_image([{"size": "xl", "url": "https://img.test/x.jpg"}]) == "https://img.test/x.jpg"


def test_parse_alpaca_carries_the_image_or_none():
    got = ns.parse_alpaca({"news": [_item([{"size": "thumb", "url": "https://img.test/t.jpg"}])]}, "FSLY")
    assert got[0]["image_url"] == "https://img.test/t.jpg"
    assert ns.parse_alpaca({"news": [_item()]}, "FSLY")[0]["image_url"] is None
