// File: frontend/src/lib/labFormat.ts
// Pure formatting for the Quant Lab. A missing value is shown as an em dash, never as zero.
export function fmtR(x: number | null | undefined, digits = 3): string {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  const v = x.toFixed(digits);
  return x > 0 ? `+${v}` : x < 0 ? `−${v.slice(1)}` : v;
}

export function fmtPct(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  const v = (x * 100).toFixed(digits);
  return x > 0 ? `+${v}%` : x < 0 ? `−${v.slice(1)}%` : `${v}%`;
}

export function fmtInterval(ci: number[] | null | undefined, f: (x: number) => string = (x) => fmtR(x)): string {
  return ci && ci.length === 2 ? `${f(ci[0])} to ${f(ci[1])}` : '—';
}

/** True when a 95% interval excludes zero, i.e. the sign is supported by the sample. */
export function excludesZero(ci: number[] | null | undefined): boolean {
  return !!ci && ci.length === 2 && (ci[0] > 0 || ci[1] < 0);
}

export function fmtInt(x: number | null | undefined): string {
  return x === null || x === undefined ? '—' : x.toLocaleString('en-US');
}
