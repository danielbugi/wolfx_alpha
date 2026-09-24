"""Promo images: valid PNGs of the promised size, readable colours (WCAG contrast), and copy free of advice words / performance claims."""
import io
import os
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

from alerts import market_card as mc  # noqa: E402
from alerts import promo_assets as pa  # noqa: E402

BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha|returns?|beat\w*|outperform\w*|earn\w*|money|rich|moon)\b", re.I)


def _img(data):
    return Image.open(io.BytesIO(data))


def test_promo_post_is_a_4_by_5_png():
    im = _img(pa.render_promo())
    assert im.format == "PNG" and im.size == (1080, 1350) and im.mode == "RGB"


def test_bot_avatar_and_channel_logo_are_square_pngs_and_differ():
    for fn in (pa.render_bot_avatar, pa.render_channel_logo):
        assert _img(fn()).size == (640, 640) and _img(fn()).format == "PNG"
        assert _img(fn(320)).size == (320, 320)
    assert pa.render_bot_avatar() != pa.render_channel_logo()                             # told apart at a glance


@pytest.mark.parametrize("fn", [pa.render_bot_avatar, pa.render_channel_logo])
def test_avatars_keep_their_corners_dark_so_the_circle_crop_shows_only_the_mark(fn):
    im = _img(fn()).convert("RGB")
    for xy in ((0, 0), (639, 0), (0, 639), (639, 639), (20, 320), (620, 320), (320, 20)):
        r, g, b = im.getpixel(xy)
        assert (r + g + b) / 3 < 90, (xy, (r, g, b))                                   # background only, no drawing near the edge
    circle_only = [(x, y) for x in range(0, 640, 8) for y in range(0, 640, 8) if (x - 320) ** 2 + (y - 320) ** 2 > 300 ** 2]
    assert all(sum(im.getpixel(p)) / 3 < 120 for p in circle_only)                      # nothing bright outside the circular crop


def test_promo_uses_the_market_card_palette_so_the_brand_matches():
    assert (pa.UP, pa.DOWN, pa.SURFACE, pa.TILE, pa.INK) == (mc.UP, mc.DOWN, mc.SURFACE, mc.TILE, mc.INK)
    im = _img(pa.render_promo())
    assert im.getpixel((5, 1340))[2] > im.getpixel((5, 1340))[0]                        # a navy background, not a light one


@pytest.mark.parametrize("fg,bg,label", [(pa.INK, pa.SURFACE, "headline"), (pa.INK2, pa.SURFACE, "subline / body"),
                                         (pa.MUTED, pa.SURFACE, "footer + small labels"), (pa.INK, pa.TILE, "panel text"),
                                         (pa.INK2, pa.TILE, "panel secondary"), (pa.MUTED, pa.TILE, "panel labels"),
                                         (pa.SURFACE, pa.INK, "CTA text"), ("#2b3f72", pa.INK, "CTA sub-text"),
                                         (pa.UP, pa.TILE, "up marks"), (pa.DOWN, pa.TILE, "down marks")])
def test_text_and_marks_meet_wcag_contrast(fg, bg, label):
    assert pa.contrast(fg, bg) >= 4.5, (label, round(pa.contrast(fg, bg), 2))


def test_the_copy_has_no_advice_words_or_performance_claims_and_labels_the_screens_as_examples():
    joined = " ".join(pa.PROMO_COPY.values())
    assert BANNED.findall(joined) == [], BANNED.findall(joined)
    assert "Example" in pa.PROMO_COPY["footer"] and "investment advice" not in joined      # the disclaimer lives in the pinned post
    assert "%" not in joined and "$" not in joined                                      # no numbers in the copy itself (screens are labelled examples)


def test_the_cta_tells_the_truth_about_access():
    """Access is on request and limited: the image must say exactly that, not 'sign up', 'beta', 'free' or a price."""
    cta = pa.PROMO_COPY["cta"].lower()
    assert "on request" in cta and "seats limited" in cta and not re.search(r"\b(sign ?up|join now|instant|open to all|subscribe|beta|free)\b|\$\d", cta)


def test_rendering_is_deterministic():
    assert pa.render_promo() == pa.render_promo() and pa.render_bot_avatar() == pa.render_bot_avatar()
