import React from 'react';
import { describe, expect, it, vi } from 'vitest';
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

describe('OwnerOrderTicket', () => {
  it('shows the plan, sizes it from the account entered, and builds the ticket for an owner', () => {
    auth.value = { user: { role: 'owner' } };
    window.localStorage.clear();
    const { container } = render(<OwnerOrderTicket detail={detail()} />);
    expect(container.textContent).toMatch(/Trade plan/);
    expect(container.textContent).toMatch(/Enter your account size/);
    fireEvent.change(screen.getByPlaceholderText('e.g. 50000'), { target: { value: '100000' } });
    // 1% of 100k = $1,000 / $5 = 200 shares = $20k, held to 10% = $10k = 100 shares
    expect(container.textContent).toMatch(/100\s*Position/);
    expect(container.textContent).toContain('BUY 100 ACME LIMIT 100.00 | STOP 95.00');
  });
  it.each([['collaborator'], ['viewer']])('renders nothing for a %s', (role) => {
    auth.value = { user: { role } };
    expect(render(<OwnerOrderTicket detail={detail()} />).container.textContent).toBe('');
  });
  it('renders nothing when signed out, without a provider, or without an active signal', () => {
    auth.value = null;
    expect(render(<OwnerOrderTicket detail={detail()} />).container.textContent).toBe('');
    auth.value = { user: { role: 'owner' } };
    expect(render(<OwnerOrderTicket detail={detail({ data_source: 'no_signal' })} />).container.textContent).toBe('');
  });
});
