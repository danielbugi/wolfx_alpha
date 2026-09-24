// File: frontend/src/lib/telegramFormat.ts
// Formatting helpers for the Telegram Control Center. The timezone always comes from the API (ALERTS_TIMEZONE), never from the browser,
// so a post's time reads the same as in the channel's own schedule.

const EMPTY = '—';

export function formatWhen(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return EMPTY;
  return new Intl.DateTimeFormat('en-GB', {
    timeZone,
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

export function formatFull(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return EMPTY;
  return new Intl.DateTimeFormat('en-GB', {
    timeZone,
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

/** "in 5 h 12 min" / "in 40 min" until an instant, or null once it has passed. */
export function timeUntil(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const minutes = Math.floor((new Date(iso).getTime() - now) / 60000);
  if (minutes <= 0) return null;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `in ${h} h ${m} min` : `in ${m} min`;
}

/**
 * The length Telegram checks (the same rule as alerts/telegram_client.py text_length): the text AFTER tags are removed and entities decoded,
 * counted in Unicode code points. A link's URL lives in the tag, so it does not count.
 */
export function telegramTextLength(html: string): number {
  const stripped = html.replace(/<[^>]+>/g, '');
  const decoded =
    typeof DOMParser === 'undefined'
      ? stripped
      : (new DOMParser().parseFromString(stripped, 'text/html').documentElement.textContent ?? '');
  return Array.from(decoded).length;
}
