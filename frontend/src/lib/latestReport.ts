/** The most recent earnings report that already has results, set against the price action around it. Pure, so it is testable. */

export interface ReportEntry {
  date: string;
  eps_estimate: number | null;
  eps_actual: number | null;
  surprise_pct: number | null;
}

export interface Bar {
  date: string;
  close: number;
}

export interface LatestReport {
  date: string;
  daysAgo: number;
  epsActual: number;
  epsEstimate: number | null;
  surprisePct: number | null;
  beat: boolean | null;
  /** Same quarter a year earlier, when it is on record with an actual. */
  yearAgo: { date: string; eps: number } | null;
  /** Close of the last session before the report date to the latest close; null when the bars do not bracket it. */
  sinceReportPct: number | null;
  /** The previous results, newest first (up to 4, excluding the latest), for the beat/miss streak. */
  previous: { date: string; beat: boolean | null; surprisePct: number | null }[];
}

const DAY = 86_400_000;
const toTime = (iso: string) => new Date(`${iso}T00:00:00`).getTime();

export function latestReport(entries: ReportEntry[], bars: Bar[], today: Date = new Date()): LatestReport | null {
  const t0 = new Date(today);
  t0.setHours(0, 0, 0, 0);
  const reported = entries
    .filter((e) => e.eps_actual != null && toTime(e.date) <= t0.getTime())
    .sort((a, b) => b.date.localeCompare(a.date));
  const last = reported[0];
  if (!last || last.eps_actual == null) return null;

  const beatOf = (e: ReportEntry) => (e.eps_actual != null && e.eps_estimate != null ? e.eps_actual > e.eps_estimate : null);

  const target = toTime(last.date) - 365 * DAY;
  const ya = reported.find((e) => Math.abs(toTime(e.date) - target) <= 20 * DAY);

  // a report dated on a weekend/holiday lands on the next session; "before" is still the session before that
  const i = bars.findIndex((b) => b.date >= last.date);
  const sinceReportPct = i > 0 && bars.length > 0 ? (bars[bars.length - 1].close / bars[i - 1].close - 1) * 100 : null;

  return {
    date: last.date,
    daysAgo: Math.round((t0.getTime() - toTime(last.date)) / DAY),
    epsActual: last.eps_actual,
    epsEstimate: last.eps_estimate,
    surprisePct: last.surprise_pct,
    beat: beatOf(last),
    yearAgo: ya && ya.eps_actual != null ? { date: ya.date, eps: ya.eps_actual } : null,
    sinceReportPct,
    previous: reported.slice(1, 5).map((e) => ({ date: e.date, beat: beatOf(e), surprisePct: e.surprise_pct })),
  };
}

/** True when the newest financial-statement quarter on file ended long before the report, i.e. its statements have not arrived yet. */
export function statementsPending(reportDate: string, latestQuarterEnd: string | null): boolean {
  if (!latestQuarterEnd) return true;
  return toTime(latestQuarterEnd) < toTime(reportDate) - 45 * DAY;
}
