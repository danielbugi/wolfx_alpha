'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Chip } from '@nextui-org/react';
import { clsx } from 'clsx';
import type { StrategyPlan } from '@/services/api';
import { defaultLevels, orderTicketText, positionSize, rTargets } from '@/lib/positionSize';

const ACCOUNT_KEY = 'plan_account_size';
const RISK_KEY = 'plan_risk_pct';
const RISK_CHOICES = [0.25, 0.5, 1, 2];

const money = (v: number) => `$${v.toFixed(2)}`;
const pct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
const num = (s: string) => {
  const v = Number(s.replace(/,/g, ''));
  return s.trim() !== '' && Number.isFinite(v) ? v : null;
};

/** The two browser-stored inputs (account size, risk per trade). Per-viewer convenience only; works without storage. */
function useSizing() {
  const [account, setAccount] = useState<string>('');
  const [risk, setRisk] = useState<number>(1);
  useEffect(() => {
    try {
      const a = window.localStorage.getItem(ACCOUNT_KEY);
      if (a) setAccount(a);
      const r = Number(window.localStorage.getItem(RISK_KEY));
      if (RISK_CHOICES.includes(r)) setRisk(r);
    } catch {
      /* storage blocked: start empty */
    }
  }, []);
  const save = useCallback((key: string, value: string) => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* ignore */
    }
  }, []);
  return {
    account,
    risk,
    setAccount: (v: string) => {
      setAccount(v);
      save(ACCOUNT_KEY, v);
    },
    setRisk: (v: number) => {
      setRisk(v);
      save(RISK_KEY, String(v));
    },
  };
}

function CopyButton({ text, label, className, disabled }: { text: string; label: string; className?: string; disabled?: boolean }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // older mobile browsers: select-and-copy fallback
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
      } catch {
        /* nothing more to try */
      }
      document.body.removeChild(ta);
    }
    setDone(true);
    window.setTimeout(() => setDone(false), 1500);
  };
  return (
    <button
      type="button"
      onClick={copy}
      disabled={disabled}
      aria-label={label}
      className={clsx(
        'min-h-[44px] rounded-xl px-3 text-[13px] font-semibold transition active:scale-[0.98] disabled:opacity-40',
        done ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-700 hover:bg-slate-200',
        className
      )}
    >
      {done ? 'Copied' : label}
    </button>
  );
}

function LevelInput({ label, value, onChange, sub, tone }: { label: string; value: string; onChange: (v: string) => void; sub: string; tone: string }) {
  return (
    <label className="block rounded-xl bg-slate-50 px-3 py-2.5">
      <span className="text-[11px] font-medium text-slate-500">{label}</span>
      <input
        inputMode="decimal"
        autoComplete="off"
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/[^0-9.]/g, ''))}
        className={clsx('mt-0.5 block h-9 w-full bg-transparent text-xl font-semibold tabular-nums outline-none', tone)}
      />
      <span className="text-[11px] tabular-nums text-slate-400">{sub}</span>
    </label>
  );
}

function Target({ label, price, entry, tone }: { label: string; price: number; entry: number; tone: string }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
      <p className="text-[11px] font-medium text-slate-500">{label}</p>
      <p className={clsx('text-[15px] font-semibold tabular-nums', tone)}>{money(price)}</p>
      <p className="text-[11px] tabular-nums text-slate-400">{pct(((price - entry) / entry) * 100)}</p>
    </div>
  );
}

/**
 * An order ticket for a phone: the numbers that go into a broker's order screen come first and are large (entry, stop,
 * shares), each copyable, and one button copies the whole ticket. With a signal plan the levels start from the plan's;
 * without one they start at the last price with a stop 2 ATR away. Either way the levels are editable, and the share count
 * is computed from the account size and risk per trade the viewer enters (kept in this browser only). Nothing is sized until
 * an account size is entered, and nothing is sent anywhere: the broker's own ticket is where the order is placed.
 */
export default function OrderTicketCard({
  symbol,
  plan,
  price,
  atr,
  score,
}: {
  symbol: string;
  plan: StrategyPlan | null;
  price: number | null;
  atr: number | null;
  score?: React.ReactNode;
}) {
  const { account, risk, setAccount, setRisk } = useSizing();
  const [direction, setDirection] = useState<'long' | 'short'>(plan?.direction ?? 'long');
  const initial = plan ? { entry: plan.entry_price, stop: plan.stop_loss_price } : defaultLevels(direction, price, atr);
  const [entryStr, setEntryStr] = useState(initial ? initial.entry.toFixed(2) : '');
  const [stopStr, setStopStr] = useState(initial ? initial.stop.toFixed(2) : '');

  const flip = (d: 'long' | 'short') => {
    setDirection(d);
    const lv = defaultLevels(d, price, atr);
    if (lv) {
      setEntryStr(lv.entry.toFixed(2));
      setStopStr(lv.stop.toFixed(2));
    }
  };
  const reset = () => {
    const lv = plan ? { entry: plan.entry_price, stop: plan.stop_loss_price } : defaultLevels(direction, price, atr);
    if (lv) {
      setEntryStr(lv.entry.toFixed(2));
      setStopStr(lv.stop.toFixed(2));
    }
  };

  const entry = num(entryStr);
  const stop = num(stopStr);
  const long = direction === 'long';
  const levelsOk = entry != null && stop != null && entry > 0 && stop > 0 && entry !== stop;
  const sideOk = levelsOk && (long ? stop! < entry! : stop! > entry!);
  const planUnedited = plan != null && entry === plan.entry_price && stop === plan.stop_loss_price;
  const targets: [number, number, number] | null = !sideOk
    ? null
    : planUnedited
      ? [plan!.tp1, plan!.tp2, plan!.tp3]
      : rTargets(entry!, stop!);
  const accountNum = num(account);
  const size = sideOk ? positionSize(accountNum, risk, entry!, stop!) : null;
  const ticket = sideOk ? orderTicketText({ symbol, direction, shares: size?.shares ?? null, entry: entry!, stop: stop!, targets }) : '';
  const edited = initial != null && (entryStr !== initial.entry.toFixed(2) || stopStr !== initial.stop.toFixed(2));

  if (!initial && !levelsOk) {
    return <p className="text-sm text-slate-400">No price or ATR is available for this stock yet, so no order ticket can be prepared.</p>;
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="inline-flex items-center gap-2 text-[15px] font-semibold text-slate-900">
          Order ticket
          {plan ? (
            <Chip size="sm" className={clsx('text-xs', long ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-700')}>
              {long ? 'Long' : 'Short'} · signal plan
            </Chip>
          ) : (
            <span className="inline-flex rounded-full bg-slate-100 p-0.5" role="group" aria-label="Side">
              {(['long', 'short'] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  aria-pressed={direction === d}
                  onClick={() => flip(d)}
                  className={clsx('min-h-[32px] rounded-full px-3 text-xs font-semibold', direction === d ? (d === 'long' ? 'bg-emerald-600 text-white' : 'bg-red-600 text-white') : 'text-slate-600')}
                >
                  {d === 'long' ? 'Buy' : 'Short'}
                </button>
              ))}
            </span>
          )}
        </h3>
        {score}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <LevelInput label={long ? 'Buy limit (entry)' : 'Sell short limit (entry)'} value={entryStr} onChange={setEntryStr} sub={price != null ? `last close ${money(price)}` : 'enter a price'} tone="text-slate-900" />
        <LevelInput
          label="Stop loss"
          value={stopStr}
          onChange={setStopStr}
          sub={sideOk ? `${pct(((stop! - entry!) / entry!) * 100)} from entry` : 'enter a stop'}
          tone="text-red-600"
        />
      </div>
      {levelsOk && !sideOk && <p className="mt-2 text-xs text-red-600">The stop must be {long ? 'below' : 'above'} the entry for a {long ? 'buy' : 'short'}.</p>}
      <p className="mt-1.5 text-[11px] text-slate-400">
        {plan ? 'Starts at the signal plan’s levels.' : 'Starts at the last close with a stop 2 ATR away; these are your levels, not a signal.'}{' '}
        {edited && (
          <button type="button" onClick={reset} className="font-semibold text-accent underline">
            Reset
          </button>
        )}
      </p>

      {targets && entry != null && stop != null && (
        <div className="mt-2 grid grid-cols-3 gap-2">
          <Target label={planUnedited ? 'Target 1' : 'Target 1 · 1R'} price={targets[0]} entry={entry} tone="text-emerald-600" />
          <Target label={planUnedited ? 'Target 2' : 'Target 2 · 2R'} price={targets[1]} entry={entry} tone="text-emerald-600" />
          <Target label={planUnedited ? 'Target 3' : 'Target 3 · 3R'} price={targets[2]} entry={entry} tone="text-emerald-600" />
        </div>
      )}

      <div className="mt-3 rounded-xl border border-slate-200/80 p-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-[11px] font-medium text-slate-500">Account size ($)</span>
            <input
              inputMode="decimal"
              autoComplete="off"
              placeholder="e.g. 50000"
              value={account}
              onChange={(e) => setAccount(e.target.value.replace(/[^0-9.,]/g, ''))}
              className="mt-1 h-11 w-full rounded-lg border border-slate-200 bg-white px-3 text-[16px] tabular-nums text-slate-900"
            />
          </label>
          <div>
            <span className="text-[11px] font-medium text-slate-500">Risk per trade</span>
            <div className="mt-1 grid grid-cols-4 gap-1" role="group" aria-label="Risk per trade">
              {RISK_CHOICES.map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setRisk(r)}
                  aria-pressed={risk === r}
                  className={clsx('h-11 whitespace-nowrap rounded-lg px-0 text-[12px] font-semibold tabular-nums', risk === r ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700')}
                >
                  {r}%
                </button>
              ))}
            </div>
          </div>
        </div>

        {size ? (
          <div className="mt-3 grid grid-cols-3 gap-2 text-center">
            <div>
              <p className="text-[11px] text-slate-500">Shares</p>
              <p className="text-2xl font-semibold tabular-nums text-slate-900">{size.shares}</p>
            </div>
            <div>
              <p className="text-[11px] text-slate-500">Position</p>
              <p className="text-[15px] font-semibold tabular-nums text-slate-900">${Math.round(size.positionValue).toLocaleString('en-US')}</p>
            </div>
            <div>
              <p className="text-[11px] text-slate-500">Max loss</p>
              <p className="text-[15px] font-semibold tabular-nums text-red-600">${Math.round(size.maxLoss).toLocaleString('en-US')}</p>
            </div>
          </div>
        ) : (
          <p className="mt-3 text-xs text-slate-400">
            {!sideOk
              ? 'Fix the levels above to get a share count.'
              : account.trim() === ''
                ? 'Enter your account size to get the share count.'
                : 'This account size is too small for one share at this stop distance.'}
          </p>
        )}
        {size?.capped && <p className="mt-2 text-[11px] text-slate-400">Held to 10% of the account; the risk budget alone would allow more.</p>}
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2">
        <CopyButton text={sideOk ? entry!.toFixed(2) : ''} label="Copy entry" disabled={!sideOk} />
        <CopyButton text={sideOk ? stop!.toFixed(2) : ''} label="Copy stop" disabled={!sideOk} />
        <CopyButton text={size ? String(size.shares) : ''} label="Copy shares" disabled={!size} />
      </div>
      <CopyButton text={ticket} label="Copy full order ticket" disabled={!sideOk} className="mt-2 w-full bg-slate-900 text-white hover:bg-slate-800" />
      {ticket && <p className="mt-2 break-words text-[11px] leading-relaxed text-slate-400">{ticket}</p>}
      <p className="mt-1 text-[11px] text-slate-400">Prices are from the last close; check the live quote in your broker before sending.</p>
    </div>
  );
}
