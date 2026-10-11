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


# ---- the bot's store works before and after migration 33 ----------------------------------------------------------

class _FakeDb:
    def __init__(self, has_column):
        self.has_column, self.sql = has_column, []

    def execute_dict_query(self, sql, params=None):
        self.sql.append(sql)
        if "information_schema.columns" in sql:
            return [{"present": 1}] if self.has_column else []
        if "image_url" in sql and "NULL AS image_url" not in sql and not self.has_column:
            raise RuntimeError("column image_url does not exist")
        return []

    def get_sync_connection(self):
        db = self

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                class _Cur:
                    def execute(self, sql, params=None):
                        db.sql.append(sql)
                        if "image_url" in sql and not db.has_column:
                            raise RuntimeError("column image_url does not exist")
                return _Cur()

            def commit(self):
                pass
        return _Conn()


def _store(has_column):
    from alerts.bot_service import PgStore
    PgStore._image_col.update(present=False, checked_at=0.0)
    return PgStore(_FakeDb(has_column))


def test_bot_store_works_without_the_image_column():
    store = _store(False)
    store.news_recent("FSLY", 5)
    store.save_news("FSLY", [{"published_at": None, "headline": "H", "source": None, "url": "https://n.test/1",
                              "url_hash": "h1", "image_url": "https://img.test/t.jpg"}], True, None)
    assert not any("image_url" in q and "NULL AS" not in q and "information_schema" not in q for q in store.db.sql)


def test_bot_store_uses_the_image_column_when_present():
    store = _store(True)
    store.news_recent("FSLY", 5)
    store.save_news("FSLY", [{"published_at": None, "headline": "H", "source": None, "url": "https://n.test/1",
                              "url_hash": "h1", "image_url": "https://img.test/t.jpg"}], True, None)
    assert any(q.startswith("INSERT INTO news_items") and "image_url" in q for q in store.db.sql)
