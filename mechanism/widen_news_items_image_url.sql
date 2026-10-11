-- mechanism/widen_news_items_image_url.sql
-- Migration 33 (numbers 26-32 are taken by lab/first-light-algo).
--
-- An optional thumbnail per stored headline, for the dashboard stock page's News tab. The news provider (Alpaca)
-- sends image links for most stories; news_service.py now keeps the smallest one. Nullable: stories without an image
-- simply have none, and rows stored before this migration get theirs filled in on the symbol's next refresh
-- (save_news upserts it). Additive and idempotent: safe to re-run, nothing existing changes.

ALTER TABLE news_items ADD COLUMN IF NOT EXISTS image_url VARCHAR(1000);
