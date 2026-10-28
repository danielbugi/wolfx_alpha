/** Position sizing for the trade plan: shares from account size and the % of the account risked on the stop. */

export const MAX_POSITION_PCT = 10; // same cap the plan has always used: no position above 10% of the account

export interface SizeResult {
  shares: number;
  positionValue: number;
  maxLoss: number;
  /** True when the 10%-of-account cap, not the risk budget, set the share count. */
  capped: boolean;
}

/** Null when any input is missing/invalid, the stop equals the entry, or not even one share fits the budget. */
export function positionSize(
  account: number | null,
  riskPct: number,
  entry: number,
  stop: number
): SizeResult | null {
  if (account == null || !(account > 0) || !(riskPct > 0) || !(entry > 0)) return null;
  const perShare = Math.abs(entry - stop);
  if (!(perShare > 0)) return null;
  const byRisk = Math.floor((account * riskPct) / 100 / perShare);
  const byCap = Math.floor((account * MAX_POSITION_PCT) / 100 / entry);
  const shares = Math.min(byRisk, byCap);
  if (shares < 1) return null;
  return { shares, positionValue: shares * entry, maxLoss: shares * perShare, capped: byCap < byRisk };
}

/** One line to paste into a broker's order ticket (or a note); the broker's own ticket is the source of truth. */
export function orderTicketText(opts: {
  symbol: string;
  direction: 'long' | 'short';
  shares: number | null;
  entry: number;
  stop: number;
  tp1: number;
  tp2: number;
  tp3: number;
}): string {
  const side = opts.direction === 'long' ? 'BUY' : 'SELL SHORT';
  const qty = opts.shares != null ? `${opts.shares} ` : '';
  const p = (v: number) => v.toFixed(2);
  return `${side} ${qty}${opts.symbol} LIMIT ${p(opts.entry)} | STOP ${p(opts.stop)} | TP1 ${p(opts.tp1)} / TP2 ${p(opts.tp2)} / TP3 ${p(opts.tp3)}`;
}
