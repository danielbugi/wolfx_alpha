// File: frontend/src/hooks/useMomentumToday.ts
// Loads the session for Momentum Today (and the Overview's top stocks) once: the three boards from the Screener's shared
// engine, paged until complete, plus the session's list ranks. Stocks that made the lists without being a breakout or a
// new high are fetched from the same engine by symbol, so every row carries the same columns. List ranks are attached
// only when they are for the same session as the boards; never mixed across sessions.
import { useEffect, useMemo, useState } from 'react';
import { QueryPairs, screenerV2Api, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';
import { DayLists, getDayLists } from '@/services/dayListsApi';
import { BOARDS, BoardKey, MomentumRow, rankByStrength, rankRows, unionRows } from '@/lib/momentumToday';

const MAX_PAGES = 5;
const SYMBOLS_PER_REQUEST = 50;

/** One board from the shared engine, paged until complete (the engine serves at most 200 rows a page). */
export async function searchAll(pairs: QueryPairs): Promise<ScreenerResult> {
  const first = await screenerV2Api.search(pairs);
  const seen = new Set(first.rows.map((r) => r.symbol));
  const rows = [...first.rows];
  for (let page = 2; rows.length < first.total && page <= MAX_PAGES; page++) {
    const next = await screenerV2Api.search(pairs.map(([k, v]) => [k, k === 'page' ? String(page) : v]) as QueryPairs);
    if (next.session !== first.session || next.rows.length === 0) break;
    for (const r of next.rows) {
      if (seen.has(r.symbol)) continue;
      seen.add(r.symbol);
      rows.push(r);
    }
  }
  return { ...first, rows };
}

/** The given symbols' rows from the shared engine for one session (requests of at most 50 symbols). */
async function bySymbols(symbols: string[], session: string): Promise<ScreenerRow[]> {
  const out: ScreenerRow[] = [];
  for (let i = 0; i < symbols.length; i += SYMBOLS_PER_REQUEST) {
    const chunk = symbols.slice(i, i + SYMBOLS_PER_REQUEST);
    const r = await screenerV2Api.search([['session', session], ...chunk.map((s) => ['symbol', s] as [string, string]), ['page', '1'], ['page_size', '200']]);
    if (r.session === session) out.push(...r.rows);
  }
  return out;
}

export type BoardState = ScreenerResult | 'error' | undefined;

export function useMomentumToday() {
  const [results, setResults] = useState<Partial<Record<BoardKey, ScreenerResult | 'error'>>>({});
  const [lists, setLists] = useState<DayLists | 'error' | null>(null);
  const [listOnly, setListOnly] = useState<ScreenerRow[]>([]);

  useEffect(() => {
    let cancelled = false;
    getDayLists()
      .then((l) => !cancelled && setLists(l))
      .catch(() => !cancelled && setLists('error'));
    for (const b of BOARDS) {
      searchAll(b.pairs)
        .then((r) => !cancelled && setResults((m) => ({ ...m, [b.key]: r })))
        .catch(() => !cancelled && setResults((m) => ({ ...m, [b.key]: 'error' })));
    }
    return () => {
      cancelled = true;
    };
  }, []);

  const ok = (k: BoardKey) => (results[k] && results[k] !== 'error' ? (results[k] as ScreenerResult) : null);
  const any = ok('breakouts') ?? ok('new_highs') ?? ok('strongest');
  const session = any?.session ?? null;
  const allFailed = BOARDS.every((b) => results[b.key] === 'error');
  const moversLoaded = results.breakouts !== undefined && results.new_highs !== undefined;
  const listsOk = lists && lists !== 'error' && session && lists.session === session ? lists : null;

  // Stocks on the session's lists that are neither a breakout nor a new high (e.g. a near-breakout that topped the lists).
  useEffect(() => {
    if (!listsOk || !moversLoaded || !session) return;
    const have = new Set([...(ok('breakouts')?.rows ?? []), ...(ok('new_highs')?.rows ?? [])].map((r) => r.symbol));
    const missing = [...listsOk.bySymbol.keys()].filter((s) => !have.has(s));
    if (missing.length === 0) return;
    let cancelled = false;
    bySymbols(missing, session)
      .then((rows) => !cancelled && setListOnly(rows))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [listsOk, moversLoaded, session]); // eslint-disable-line react-hooks/exhaustive-deps

  const attach = (rows: MomentumRow[]) =>
    rows.map((r) => {
      const e = listsOk?.bySymbol.get(r.symbol);
      return e ? { ...r, list_ranks: e.list_ranks, top_two: e.top_two } : r;
    });

  const movers = useMemo(
    () => rankRows(attach(unionRows({ breakouts: ok('breakouts')?.rows, new_highs: ok('new_highs')?.rows, lists: listOnly }))),
    [results, listOnly, listsOk], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const strength = useMemo(
    () => rankByStrength(attach((ok('strongest')?.rows ?? []).map((r) => ({ ...r, group: 'strongest' as const })))),
    [results, listsOk], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const listsNote =
    lists === null || !session
      ? null
      : lists === 'error'
        ? "The session's list ranks are unavailable right now, so Movers are ordered by the 1-day gain only."
        : lists.session !== session
          ? `The list ranks on hand are for ${lists.session ?? 'an earlier session'}, not ${session}, so Movers are ordered by the 1-day gain only.`
          : null;

  return {
    results,
    ok,
    any,
    session,
    allFailed,
    moversState: (results.breakouts === 'error' && results.new_highs === 'error' ? 'error' : moversLoaded ? 'ready' : 'loading') as 'loading' | 'ready' | 'error',
    strengthState: (results.strongest === undefined ? 'loading' : results.strongest === 'error' ? 'error' : 'ready') as 'loading' | 'ready' | 'error',
    movers,
    strength,
    listsNote,
  };
}
