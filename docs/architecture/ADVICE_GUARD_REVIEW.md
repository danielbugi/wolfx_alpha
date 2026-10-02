# Advice-wording guard — review of its weaknesses

> **Status: observation only. The guard was NOT changed** (decision D4: "do not weaken the existing production advice guard in this
> implementation"). This document records what the guard does and does not do so that any future change is a deliberate, tested one.
> Policy source: [CHANNEL_VOICE_AND_COPY.md](../../CHANNEL_VOICE_AND_COPY.md) and `PRIVATE_ASSISTANT_PLAN.md` §0.

## What the guard is

A word-boundary, case-insensitive regex named `BANNED`, declared **separately in about a dozen test modules** under
`mechanism/alerts/tests/` (`test_channel_content`, `test_board`, `test_insights`, `test_morning`, `test_channel_tools`,
`test_promo_assets`, `test_market_environment`, `test_bot`, `test_bot_qa`, `test_assistant_flows`, `test_access_flow`, …). Each test
renders its own content and asserts `BANNED.findall(plain_text) == []`.

Core terms: `buy*, sell*, target(s), stop(s), entry/entries, recommend*, signal(s), opportunit*, pick(s), should, profit*, winner(s),
guarantee*, alpha`. The channel-facing copies add `return(s), beat*, outperform*, earn*, money`; `test_promo_assets` also adds `rich, moon`.

## Weaknesses (none fixed here)

1. **It exists only in tests.** No runtime code checks outgoing text. A new builder, a data-driven string (a symbol's company name, a
   sector name, a vendor headline) or a hand-sent message is not guarded unless a test happens to render it with that data. The guard
   proves "these fixtures render clean", not "nothing sent can contain these words".
2. **Twelve copies that have drifted.** The assistant/bot/access-flow copies lack `return(s)/beat/outperform/earn/money`; the promo copy
   alone has `rich/moon`. There is no single source, so adding a term means editing every copy and a stale copy silently accepts it.
3. **Lexical, not semantic.** Advice phrased without those words passes: "now is a good time to add", "worth owning", "load up",
   "avoid", "get in/out", "cheap", "can't lose", "safe", "undervalued", "top pick" (only `pick` is caught), "strong conviction". Imperatives
   and implied advice are invisible to it. It also cannot see a *forecast* ("will rise", "expect higher").
4. **Morphology gaps.** `\w*` stems cover `buy/sell/recommend/profit/guarantee/…` but not inflections of other terms: `target` is matched
   as `targets?` only (`targeted` passes), `stop` as `stops?` only (`stopped`, `stopping` pass), `winner` as `winners?` only (`winning`,
   `won` pass), `signal` as `signals?` only (`signalling`, `signaled` pass).
5. **Over-blocking of legitimate data words.** `return(s)` and `earn*` are banned in channel copy, which forces circumlocution for
   neutral facts (a *price return* is described as "change"; *earnings* data needs care). That is why the Market Environment post says
   "change" and "sessions" — and why the **domain** terms (`RISK_ON`, regime, returns) are allowed in the API/dashboard but kept out of
   Telegram headings. The guard is a wording policy, not a statement about which concepts are advice.
6. **HTML/entity handling is per test.** Tests strip tags with a naive `<[^>]+>` regex. Text hidden in attributes (e.g. `href`/button
   labels) or HTML entities (`&#98;uy`) is not normalised; non-English text is not covered at all.
7. **Scope.** Image captions, button labels, command help and the pinned START_HERE are only guarded where a test covers them; the
   dashboard/API are deliberately out of scope.
8. **Possibly no tests of the regex itself.** This review did not find a test asserting that each copy *does* catch each banned term (not
   exhaustively verified), so an accidental edit that loosens a copy may not fail anything.

## If it is ever tightened (suggested order, not scheduled)

1. One shared module (single source of truth) imported by every test, with its own tests proving each term is caught and each
   intended-neutral phrasing passes.
2. A runtime check at the single send choke point (`post_delivery` / the sender), failing closed for channel sends.
3. Add forecast/imperative patterns only with a review of false positives against real rendered posts.

Any of these changes channel behaviour and needs an explicit owner decision.
