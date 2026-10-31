/** Position sizing for the order ticket: shares from account size and the % of the account risked on the stop. */

export const MAX_POSITION_PCT = 10; // no position above 10% of the account
export const DEFAULT_STOP_ATR_MULTIPLE = 2;

export interface SizeResult {
  shares: number;
  positionValue: number;
  maxLoss: number;
  /** True when the 10%-of-account cap, not the risk budget, set the share count. */
  capped: boolean;
}

/** Null when any input is missing/invalid, the stop equals the entry, or not even one share fits the budget. */
export function positionSize(account: number | null, riskPct: number, entry: number, stop: number): SizeResult | null {
  if (account == null || !(account > 0) || !(riskPct > 0) || !(entry > 0) || !(stop > 0)) return null;
  const perShare = Math.abs(entry - stop);
  if (!(perShare > 0)) return null;
  const byRisk = Math.floor((account * riskPct) / 100 / perShare);
  const byCap = Math.floor((account * MAX_POSITION_PCT) / 100 / entry);
  const shares = Math.min(byRisk, byCap);
  if (shares < 1) return null;
  return { shares, positionValue: shares * entry, maxLoss: shares * perShare, capped: byCap < byRisk };
}

/** Starting levels when the stock has no signal plan: the last price and a stop `multiple` ATRs away. Null without both. */
export function defaultLevels(
  direction: 'long' | 'short',
  price: number | null | undefined,
  atr: number | null | undefined,
  multiple: number = DEFAULT_STOP_ATR_MULTIPLE
): { entry: number; stop: number } | null {
  if (price == null || atr == null || !(price > 0) || !(atr > 0)) return null;
  const stop = direction === 'long' ? price - multiple * atr : price + multiple * atr;
  if (!(stop > 0)) return null;
  return { entry: price, stop };
}

/** Targets at 1R / 2R / 3R from the levels (R = the distance from entry to stop); they fall on the side the stop implies. */
export function rTargets(entry: number, stop: number): [number, number, number] | null {
  const r = entry - stop;
  if (!(entry > 0) || !(stop > 0) || r === 0) return null;
  return [entry + r, entry + 2 * r, entry + 3 * r];
}

/** One line to paste into a broker's order ticket (or a note); the broker's own ticket is the source of truth. */
export function orderTicketText(opts: {
  symbol: string;
  direction: 'long' | 'short';
  shares: number | null;
  entry: number;
  stop: number;
  targets: [number, number, number] | null;
}): string {
  const side = opts.direction === 'long' ? 'BUY' : 'SELL SHORT';
  const qty = opts.shares != null ? `${opts.shares} ` : '';
  const p = (v: number) => v.toFixed(2);
  const tp = opts.targets ? ` | TP1 ${p(opts.targets[0])} / TP2 ${p(opts.targets[1])} / TP3 ${p(opts.targets[2])}` : '';
  return `${side} ${qty}${opts.symbol} LIMIT ${p(opts.entry)} | STOP ${p(opts.stop)}${tp}`;
}
