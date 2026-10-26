// File: frontend/src/lib/earningsConfidence.ts
// One wording for an upcoming earnings date the latest calendar check did not confirm (backend
// services/earnings_read_model.py: confidence "unverified" = previously reported by an earlier check, not listed by the
// latest COMPLETE check). Such a date is never shown as an ordinary countdown -- every surface (Stock badge and tab,
// chart markers, Watchlist chart, Earnings page, Screener rows) uses these helpers.

export const UNVERIFIED_NOTE = 'not confirmed by latest check';

/** True only for the explicit "unverified" state (an older backend sends no confidence: treated as before). */
export const isUnverified = (confidence: string | null | undefined): boolean => confidence === 'unverified';

/** "Oct 28" for an ISO date (UTC, so the calendar day never shifts). */
export function shortDay(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
}

/** "Previously reported: Oct 28 — not confirmed by latest check" */
export function previouslyReported(iso: string): string {
  return `Previously reported: ${shortDay(iso)} — ${UNVERIFIED_NOTE}`;
}
