'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Chip } from '@nextui-org/react';
import { clsx } from 'clsx';
import type { StrategyPlan } from '@/services/api';
import PlanBar from '@/components/strategy/PlanBar';
import { orderTicketText, positionSize } from '@/lib/positionSize';

const ACCOUNT_KEY = 'plan_account_size';
const RISK_KEY = 'plan_risk_pct';
const RISK_CHOICES = [0.25, 0.5, 1, 2];

const money = (v: number) => `$${v.toFixed(2)}`;
const pct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;

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

function CopyButton({ text, label, className }: { text: string; label: string; className?: string }) {
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
      aria-label={label}
      className={clsx(
        'min-h-[44px] rounded-xl px-3 text-[13px] font-semibold transition active:scale-[0.98]',
        done ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-700 hover:bg-slate-200',
        className
      )}
    >
      {done ? 'Copied' : label}
    </button>
  );
}

function Level({ label, price, sub, tone, big }: { label: string; price: number; sub: string; tone: string; big?: boolean }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
      <p className="text-[11px] font-medium text-slate-500">{label}</p>
      <p className={clsx('font-semibold tabular-nums', big ? 'text-xl' : 'text-[15px]', tone)}>{money(price)}</p>
      <p className="text-[11px] tabular-nums text-slate-400">{sub}</p>
    </div>
  );
}

/**
 * The trade plan, built for a phone: the numbers you type into a broker's order ticket come first and are large
 * (entry, stop, shares), each copyable, and one button copies the whole ticket. Sizing is computed from the account
 * size and risk per trade the viewer enters (kept in this browser only); nothing is sized until an account size is entered.
 */
export default function TradePlanCard({ symbol, plan, score }: { symbol: string; plan: StrategyPlan; score?: React.ReactNode }) {
  const { account, risk, setAccount, setRisk } = useSizing();
  const accountNum = account.trim() === '' ? null : Number(account.replace(/,/g, ''));
  const size = positionSize(accountNum != null && Number.isFinite(accountNum) ? accountNum : null, risk, plan.entry_price, plan.stop_loss_price);
  const long = plan.direction === 'long';
  const ticket = orderTicketText({
    symbol,
    direction: plan.direction,
    shares: size?.shares ?? null,
    entry: plan.entry_price,
    stop: plan.stop_loss_price,
    tp1: plan.tp1,
    tp2: plan.tp2,
    tp3: plan.tp3,
  });
  const rMultiple = (tp: number) => Math.abs(tp - plan.entry_price) / Math.abs(plan.entry_price - plan.stop_loss_price);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="inline-flex items-center gap-2 text-[15px] font-semibold text-slate-900">
          Trade plan
          <Chip size="sm" className={clsx('text-xs', long ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-700')}>
            {long ? 'Long' : 'Short'}
          </Chip>
        </h3>
        {score}
      </div>

      {/* What goes into the order ticket first, large */}
      <div className="grid grid-cols-2 gap-2">
        <Level label={long ? 'Buy limit (entry)' : 'Sell short limit (entry)'} price={plan.entry_price} sub="last close" tone="text-slate-900" big />
        <Level label="Stop loss" price={plan.stop_loss_price} sub={`${pct(-plan.risk_pct)} from entry`} tone="text-red-600" big />
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        <Level label="Target 1" price={plan.tp1} sub={`${pct(plan.reward_pct_tp1)} · ${rMultiple(plan.tp1).toFixed(1)}R`} tone="text-emerald-600" />
        <Level label="Target 2" price={plan.tp2} sub={`${pct(plan.reward_pct_tp2)} · ${rMultiple(plan.tp2).toFixed(1)}R`} tone="text-emerald-600" />
        <Level label="Target 3" price={plan.tp3} sub={`${pct(plan.reward_pct_tp3)} · ${rMultiple(plan.tp3).toFixed(1)}R`} tone="text-emerald-600" />
      </div>

      {/* Size */}
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
                  className={clsx(
                    'h-11 rounded-lg text-[13px] font-semibold tabular-nums',
                    risk === r ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700'
                  )}
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
            {account.trim() === '' ? 'Enter your account size to get the share count.' : 'This account size is too small for one share at this stop distance.'}
          </p>
        )}
        {size?.capped && <p className="mt-2 text-[11px] text-slate-400">Held to 10% of the account; the risk budget alone would allow more.</p>}
      </div>

      {/* Copy for the broker app */}
      <div className="mt-3 grid grid-cols-3 gap-2">
        <CopyButton text={plan.entry_price.toFixed(2)} label="Copy entry" />
        <CopyButton text={plan.stop_loss_price.toFixed(2)} label="Copy stop" />
        <CopyButton text={size ? String(size.shares) : ''} label="Copy shares" className={size ? '' : 'pointer-events-none opacity-40'} />
      </div>
      <CopyButton text={ticket} label="Copy full order ticket" className="mt-2 w-full bg-slate-900 text-white hover:bg-slate-800" />
      <p className="mt-2 break-words text-[11px] leading-relaxed text-slate-400">{ticket}</p>
      <p className="mt-1 text-[11px] text-slate-400">Levels are the model&apos;s, from the last close; check the live quote in your broker before sending.</p>

      <div className="mt-4 hidden sm:block">
        <PlanBar plan={{ stop_loss_price: plan.stop_loss_price, entry_price: plan.entry_price, tp1: plan.tp1, tp2: plan.tp2, tp3: plan.tp3 }} />
      </div>
    </div>
  );
}
