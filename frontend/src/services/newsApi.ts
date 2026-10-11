// Stock headlines (backend/routers/news.py). Kept out of services/api.ts so the news contract sits in one small file.
import { apiClient } from '@/services/api';

export interface NewsItem {
  published_at: string; // ISO timestamp
  headline: string;
  source: string | null;
  url: string;
  /** small thumbnail from the provider, or null (many stories have none) */
  image_url: string | null;
}

export interface StockNews {
  symbol: string;
  /** ok = headlines found · empty = provider has none · unavailable = provider or storage failing right now */
  status: 'ok' | 'empty' | 'unavailable';
  /** why it's unavailable, as a short code (e.g. provider_keys_missing, storage_error:UndefinedTable); null otherwise */
  reason?: string | null;
  /** true when `items` are older stored headlines shown because the latest fetch failed */
  stale: boolean;
  items: NewsItem[];
}

export const newsApi = {
  async get(symbol: string, limit = 8): Promise<StockNews> {
    const response = await apiClient.get<StockNews>(`/api/news/${encodeURIComponent(symbol)}`, { params: { limit } });
    return response.data;
  },
};
