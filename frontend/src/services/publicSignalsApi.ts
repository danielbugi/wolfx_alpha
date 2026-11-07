// File: frontend/src/services/publicSignalsApi.ts
// Client for the PUBLIC Fundamental Updates API (GET /api/public/signals). Unauthenticated: plain fetch, no credentials and no access token,
// so a signed-out visitor and a signed-in user make exactly the same request. The shapes mirror backend/services/public_signals_service.py,
// which returns only the allowlisted fields.
import { API_BASE_URL } from '@/services/api';

export interface PublicFigure {
  label: string;
  value: string;
  compare: string | null;
}

export interface PublicNewsItem {
  published_at: string;
  source: string;
  headline: string;
  url: string;
}

export interface PublicUpdate {
  key: string;
  symbol: string;
  company_name: string | null;
  event_type: string;
  label: string;
  quarter_end: string;
  fiscal_label: string;
  public_date: string;
  date_source: string;
  sector: string | null;
  industry: string | null;
  market_cap: { value: string; as_of: string } | null;
  figures: PublicFigure[];
  why: string;
  links: { finviz: string; detail: string; sec?: string | null; sec_kind?: 'filing' | 'company_filings' | null };
  news: PublicNewsItem[];
  content_hash: string;
  render_version: string;
}

export type PublicStatus = 'published' | 'corrected' | 'withdrawn';

export interface PublicFeedItem {
  update: PublicUpdate;
  status: PublicStatus;
  first_published_at: string;
  updated_at: string;
}

export interface PublicFeed {
  items: PublicFeedItem[];
  next_cursor: string | null;
}

export interface PublicCorrection {
  version: number;
  kind: 'restated' | 'redated' | 'withdrawn';
  changed: string[];
  at: string;
}

export interface PublicDetail {
  update: PublicUpdate;
  status: PublicStatus;
  version: number;
  first_published_at: string;
  updated_at: string;
  corrections: PublicCorrection[];
}

/** A failed request, with the HTTP status (0: network) so pages can say "not found" vs "try again later" vs "too many requests". */
export class PublicApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, { method: 'GET', credentials: 'omit', headers: { Accept: 'application/json' }, signal });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new PublicApiError(0, 'network');
  }
  if (!res.ok) throw new PublicApiError(res.status, res.statusText || 'error');
  return (await res.json()) as T;
}

export interface FeedQuery {
  limit?: number;
  cursor?: string | null;
  types?: string[];
  sector?: string | null;
}

export function feedPath(q: FeedQuery = {}): string {
  const p = new URLSearchParams();
  if (q.limit) p.set('limit', String(q.limit));
  if (q.cursor) p.set('cursor', q.cursor);
  for (const t of q.types ?? []) p.append('type', t);
  if (q.sector) p.set('sector', q.sector);
  const qs = p.toString();
  return `/api/public/signals${qs ? `?${qs}` : ''}`;
}

export function detailPath(symbol: string, quarter: string, slug: string): string {
  return `/api/public/signals/${encodeURIComponent(symbol)}/${encodeURIComponent(quarter)}/${encodeURIComponent(slug)}`;
}

export const publicSignalsApi = {
  feed: (q: FeedQuery = {}, signal?: AbortSignal) => getJson<PublicFeed>(feedPath(q), signal),
  detail: (symbol: string, quarter: string, slug: string, signal?: AbortSignal) => getJson<PublicDetail>(detailPath(symbol, quarter, slug), signal),
};
