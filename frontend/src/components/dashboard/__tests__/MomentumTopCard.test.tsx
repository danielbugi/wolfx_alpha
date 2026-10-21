// Overview card: the top five of each Momentum Today view, same data and ranking as that page, linking to each view.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import type { QueryPairs } from '@/services/screenerV2Api';
import { LISTS, searchMock } from '@/app/momentum-today/__tests__/fixtures';

const api = vi.hoisted(() => ({ search: vi.fn(), definitions: vi.fn(), exportCsv: vi.fn() }));
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: api }));
const lists = vi.hoisted(() => ({ getDayLists: vi.fn() }));
vi.mock('@/services/dayListsApi', () => lists);

import MomentumTopCard from '@/components/dashboard/MomentumTopCard';

beforeEach(() => {
  api.search.mockReset();
  api.search.mockImplementation(async (pairs: QueryPairs) => searchMock(pairs));
  lists.getDayLists.mockReset();
  lists.getDayLists.mockResolvedValue(LISTS());
});

describe('Overview: Momentum Today card', () => {
  it("shows the top of Today's Movers and Strength Leaders with links to each view", async () => {
    render(<MomentumTopCard />);
    const movers = await screen.findByTestId('top-movers');
    await waitFor(() => expect(within(movers).getAllByTestId('top-symbol').map((x) => x.textContent)).toEqual(['FFF', 'BBB', 'CCC', 'AAA', 'DDD']));
    const strength = screen.getByTestId('top-strength');
    expect(within(strength).getAllByTestId('top-symbol').map((x) => x.textContent)).toEqual(['EEE', 'AAA']);
    expect(within(movers).getByRole('link', { name: 'See all →' }).getAttribute('href')).toBe('/momentum-today?view=movers');
    expect(within(strength).getByRole('link', { name: 'See all →' }).getAttribute('href')).toBe('/momentum-today?view=strength');
    expect(within(movers).getAllByTestId('top-row')[3].textContent).toContain('Earnings 1d ago'); // AAA: why it's moving
    expect(screen.getByTestId('momentum-top').textContent).toContain('not a forecast or a recommendation');
  });

  it('renders nothing when all the data is unavailable', async () => {
    api.search.mockRejectedValue(new Error('down'));
    const { container } = render(<MomentumTopCard />);
    await waitFor(() => expect(container.querySelector('[data-testid="momentum-top"]')).toBeNull());
  });
});
