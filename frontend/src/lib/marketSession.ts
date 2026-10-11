// US market status for the sidebar clock, from the official session calendar (backend /api/market/calendar, which
// reuses the pipeline's Alpaca calendar: holidays and early closes included). Pure functions so they can be tested
// with any "now".

export interface MarketCalendar {
  timezone: string; // America/New_York
  regular_open: string; // "09:30"
  source: string;
  /** false only on the calendar's weekday fallback: a weekday is then assumed to be a session */
  holidays_known: boolean;
  sessions: { date: string; close: string }[]; // New York dates; close "16:00" or an early close like "13:00"
}

export type MarketState = 'open' | 'pre' | 'after' | 'closed';

export interface MarketStatus {
  state: MarketState;
  /** short headline, e.g. "NYSE open", "Pre-market", "Closed · holiday" */
  label: string;
  /** e.g. "closes in 2h 14m", "opens Mon 09:30" */
  detail: string;
  earlyClose: string | null;
}

const NY = 'America/New_York';
const PRE_MARKET_START = 4 * 60; // 04:00 ET
const minutesOf = (hhmm: string) => {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
};

/** Wall-clock parts of `date` in a time zone. */
export function zoneParts(date: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    weekday: 'short',
  }).formatToParts(date);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  return {
    isoDate: `${get('year')}-${get('month')}-${get('day')}`,
    minutes: Number(get('hour')) * 60 + Number(get('minute')),
    weekday: get('weekday'), // Mon, Tue, ...
  };
}

/** The instant at which New York wall-clock `isoDate hh:mm` happens (DST-correct). */
function nyInstant(isoDate: string, minutes: number): Date {
  const [y, mo, d] = isoDate.split('-').map(Number);
  const guess = new Date(Date.UTC(y, mo - 1, d, Math.floor(minutes / 60), minutes % 60) + 5 * 3600_000);
  const shown = zoneParts(guess, NY);
  const drift = (shown.isoDate === isoDate ? shown.minutes : shown.minutes + (shown.isoDate > isoDate ? 1440 : -1440)) - minutes;
  return new Date(guess.getTime() - drift * 60_000);
}

function inWords(ms: number): string {
  const mins = Math.max(0, Math.round(ms / 60_000));
  const d = Math.floor(mins / 1440);
  const h = Math.floor((mins % 1440) / 60);
  const m = mins % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function weekdayOf(isoDate: string): string {
  return new Date(`${isoDate}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' });
}

export function marketStatus(now: Date, cal: MarketCalendar): MarketStatus {
  const ny = zoneParts(now, NY);
  const sessions = new Map(cal.sessions.map((s) => [s.date, s.close]));
  const openMin = minutesOf(cal.regular_open);
  const todayClose = sessions.get(ny.isoDate);

  const nextOpen = (): string => {
    const upcoming = cal.sessions.map((s) => s.date).filter((d) => d > ny.isoDate || (d === ny.isoDate && ny.minutes < openMin)).sort()[0];
    if (!upcoming) return 'next session not in calendar';
    const at = nyInstant(upcoming, openMin);
    const when = upcoming === ny.isoDate ? `today ${cal.regular_open}` : `${weekdayOf(upcoming)} ${cal.regular_open} ET`;
    return `opens ${when} · in ${inWords(at.getTime() - now.getTime())}`;
  };

  if (todayClose) {
    const closeMin = minutesOf(todayClose);
    const early = todayClose !== '16:00' ? todayClose : null;
    const afterEnd = closeMin + 4 * 60; // extended hours run 4h past the close (20:00 ET on a normal day)
    if (ny.minutes >= openMin && ny.minutes < closeMin) {
      return {
        state: 'open',
        label: 'NYSE open',
        detail: `closes ${early ? `early at ${early} · ` : ''}in ${inWords(nyInstant(ny.isoDate, closeMin).getTime() - now.getTime())}`,
        earlyClose: early,
      };
    }
    if (ny.minutes >= PRE_MARKET_START && ny.minutes < openMin) {
      return { state: 'pre', label: 'Pre-market', detail: nextOpen(), earlyClose: early };
    }
    if (ny.minutes >= closeMin && ny.minutes < afterEnd) {
      return { state: 'after', label: 'After hours', detail: nextOpen(), earlyClose: early };
    }
    return { state: 'closed', label: 'Market closed', detail: nextOpen(), earlyClose: early };
  }

  const weekend = ny.weekday === 'Sat' || ny.weekday === 'Sun';
  return {
    state: 'closed',
    label: weekend ? 'Closed · weekend' : cal.holidays_known ? 'Closed · market holiday' : 'Market closed',
    detail: nextOpen(),
    earlyClose: null,
  };
}
