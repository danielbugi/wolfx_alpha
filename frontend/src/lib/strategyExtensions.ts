// File: frontend/src/lib/strategyExtensions.ts
// The ONE place strategy-specific presentation lives. Every Quant Lab view (funnel, explorer, candidate detail, capture
// health) is generic; what a strategy stores in its candidate `strategy_context` extension, and what it calls its
// candidate classes, is declared here by strategy_key. A strategy with no entry still renders: unknown extension fields
// are humanised and unknown classes fall back to their stored name, so registering strategy #2 never needs a new view.
import { humanize } from '@/lib/strategyFormat';

export type ExtensionValueKind = 'price' | 'text' | 'date' | 'number';

export interface ExtensionField {
  label: string;
  kind: ExtensionValueKind;
}

export interface StrategyExtension {
  /** labels for the keys of candidate.strategy_context.extension, in display order */
  contextFields: Record<string, ExtensionField>;
  /** labels for candidate classes (signal_type values) */
  candidateClasses: Record<string, string>;
}

const REGISTRY: Record<string, StrategyExtension> = {
  donchian_breakout: {
    contextFields: {
      urgency: { label: 'Urgency', kind: 'text' },
      donchian_high_20: { label: 'Donchian high (20)', kind: 'price' },
      donchian_low_20: { label: 'Donchian low (20)', kind: 'price' },
      weekly_trend: { label: 'Weekly trend', kind: 'text' },
      weekly_asof: { label: 'Weekly context as of', kind: 'date' },
      monthly_trend: { label: 'Monthly trend', kind: 'text' },
      monthly_asof: { label: 'Monthly context as of', kind: 'date' },
    },
    candidateClasses: {
      bullish_breakout: 'Bullish breakout',
      bearish_breakout: 'Bearish breakout',
      near_bullish: 'Near bullish breakout',
      near_bearish: 'Near bearish breakout',
    },
  },
};

const EMPTY: StrategyExtension = { contextFields: {}, candidateClasses: {} };

export function getExtension(strategyKey: string): StrategyExtension {
  return REGISTRY[strategyKey] ?? EMPTY;
}

export function candidateClassLabel(strategyKey: string, candidateClass: string): string {
  return getExtension(strategyKey).candidateClasses[candidateClass] ?? humanize(candidateClass);
}

export interface ExtensionRow {
  key: string;
  label: string;
  kind: ExtensionValueKind;
  value: unknown;
}

/** The extension's entries in declared order first, then any undeclared ones (humanised), so no stored field is hidden. */
export function extensionRows(strategyKey: string, extension: Record<string, unknown>): ExtensionRow[] {
  const declared = getExtension(strategyKey).contextFields;
  const rows: ExtensionRow[] = [];
  for (const [key, f] of Object.entries(declared)) {
    if (key in extension) rows.push({ key, label: f.label, kind: f.kind, value: extension[key] });
  }
  for (const [key, value] of Object.entries(extension)) {
    if (!(key in declared)) rows.push({ key, label: humanize(key), kind: typeof value === 'number' ? 'number' : 'text', value });
  }
  return rows;
}
