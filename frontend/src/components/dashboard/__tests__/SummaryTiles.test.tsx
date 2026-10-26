// "Sectors today" tile: only sectors with a numeric change are ranked; a null, missing or NaN change never crashes the
// tile or wins the "strongest" slot.
import React from 'react';
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import type { MarketOverview } from '@/services/api';
import SummaryTiles from '@/components/dashboard/SummaryTiles';

const overview = (sectors: unknown[]) =>
  ({ market_breadth: {}, market_indicators: {}, sector_performance: sectors }) as unknown as MarketOverview;

describe('SummaryTiles', () => {
  it('skips sectors without a numeric change', () => {
    const { container } = render(
      <SummaryTiles
        indices={[]}
        overview={overview([
          { sector: 'Energy', stock_count: 126, avg_performance: null },
          { sector: 'Utilities', stock_count: 74, avg_performance: Number.NaN },
          { sector: 'Technology', stock_count: 425, avg_performance: 0.45 },
          { sector: 'Healthcare', stock_count: 502, avg_performance: -1.59 },
        ])}
      />,
    );
    expect(container.textContent).toContain('Technology');
    expect(container.textContent).toContain('+0.45% · strongest');
    expect(container.textContent).toContain('-1.59%');
    expect(container.textContent).not.toMatch(/NaN|Energy|Utilities/);
  });

  it('with no usable sector it shows a dash', () => {
    const { container } = render(<SummaryTiles indices={[]} overview={overview([{ sector: 'Energy', stock_count: 9, avg_performance: undefined }])} />);
    expect(container.textContent).not.toMatch(/NaN|undefined/);
    expect(container.textContent).toContain('—');
  });
});
