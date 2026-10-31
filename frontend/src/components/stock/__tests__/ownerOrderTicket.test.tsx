import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const auth = vi.hoisted(() => ({ value: null as null | { user: { role: string } | null } }));
vi.mock('@/contexts/AuthContext', () => ({ useOptionalAuth: () => auth.value }));

import OwnerOrderTicket from '../OwnerOrderTicket';

const plan = {
  direction: 'long', entry_price: 100, stop_loss_price: 95, tp1: 105, tp2: 110, tp3: 115, risk_pct: 5, reward_pct_tp1: 5,
  reward_pct_tp2: 10, reward_pct_tp3: 15, strategy_score: 80, momentum_score: null, fundamentals_score: null, alignment_score: 80, risk_bonus: 5,
};
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const detail = (over: Record<string, unknown> = {}): any => ({ symbol: 'ACME', data_source: 'active_signal', ai_rating: { plan }, ...over });
const noSignal = () => detail({ data_source: 'no_signal', ai_rating: { plan: null } });
const owner = () => {
  auth.value = { user: { role: 'owner' } };
};
const text = (c: HTMLElement) => c.textContent ?? '';

beforeEach(() => window.localStorage.clear());

describe('OwnerOrderTicket: owner gate', () => {
  it.each([['collaborator'], ['viewer']])('renders nothing for a %s', (role) => {
    auth.value = { user: { role } };
    expect(render(<OwnerOrderTicket detail={detail()} price={100} atr={3} />).container.textContent).toBe('');
  });
  it('renders nothing when signed out or without a provider', () => {
    auth.value = null;
    expect(render(<OwnerOrderTicket detail={detail()} price={100} atr={3} />).container.textContent).toBe('');
    auth.value = { user: null };
    expect(render(<OwnerOrderTicket detail={detail()} price={100} atr={3} />).container.textContent).toBe('');
  });
});

describe('OwnerOrderTicket: with a signal plan', () => {
  it('starts from the plan, sizes it from the account entered, and builds the ticket', () => {
    owner();
    const { container } = render(<OwnerOrderTicket detail={detail()} price={100} atr={3} />);
    expect(text(container)).toMatch(/Order ticket/);
    expect(text(container)).toMatch(/signal plan/);
    expect(text(container)).toMatch(/Enter your account size/);
    fireEvent.change(screen.getByPlaceholderText('e.g. 50000'), { target: { value: '100000' } });
    // 1% of 100k = $1,000 / $5 = 200 shares = $20k, held to 10% = $10k = 100 shares
    expect(text(container)).toContain('BUY 100 ACME LIMIT 100.00 | STOP 95.00 | TP1 105.00 / TP2 110.00 / TP3 115.00');
  });
  it('switches the targets to 1R/2R/3R once the stop is edited', () => {
    owner();
    const { container } = render(<OwnerOrderTicket detail={detail()} price={100} atr={3} />);
    fireEvent.change(screen.getByLabelText('Stop loss'), { target: { value: '96' } });
    expect(text(container)).toContain('TP1 104.00 / TP2 108.00 / TP3 112.00');
    expect(text(container)).toMatch(/Reset/);
  });
});

describe('OwnerOrderTicket: a stock with no signal', () => {
  it('starts at the last price with a 2 ATR stop and lets the side be flipped', () => {
    owner();
    const { container } = render(<OwnerOrderTicket detail={noSignal()} price={100} atr={3} />);
    expect(text(container)).toContain('BUY ACME LIMIT 100.00 | STOP 94.00 | TP1 106.00 / TP2 112.00 / TP3 118.00');
    expect(text(container)).toMatch(/these are your levels, not a signal/);
    fireEvent.click(screen.getByRole('button', { name: 'Short' }));
    expect(text(container)).toContain('SELL SHORT ACME LIMIT 100.00 | STOP 106.00 | TP1 94.00 / TP2 88.00 / TP3 82.00');
  });
  it('refuses a stop on the wrong side instead of sizing it', () => {
    owner();
    const { container } = render(<OwnerOrderTicket detail={noSignal()} price={100} atr={3} />);
    fireEvent.change(screen.getByLabelText('Stop loss'), { target: { value: '104' } });
    expect(text(container)).toMatch(/The stop must be below the entry for a buy/);
    expect(text(container)).not.toMatch(/LIMIT/);
  });
  it('says so, and offers no ticket, when there is no price or ATR', () => {
    owner();
    const { container } = render(<OwnerOrderTicket detail={noSignal()} price={null} atr={null} />);
    expect(text(container)).toMatch(/no order ticket can be prepared/);
  });
});
