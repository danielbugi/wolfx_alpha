// File: frontend/src/components/dashboard/MarketSummaryCards.tsx
import React from 'react';
import { MarketSummary, formatters } from '@/services/api';
import {
  ChartBarIcon,
  ArrowTrendingUpIcon,
  BoltIcon,
  ClockIcon
} from '@heroicons/react/24/outline';

interface MarketSummaryCardsProps {
  summary: MarketSummary;
}

export default function MarketSummaryCards({ summary }: MarketSummaryCardsProps) {
  const cards = [
    {
      title: 'Total Symbols',
      value: formatters.number(summary.total_symbols),
      icon: ChartBarIcon,
      color: 'blue',
    },
    {
      title: 'Avg Volume Ratio',
      value: `${summary.avg_volume_ratio.toFixed(2)}x`,
      icon: ArrowTrendingUpIcon,
      color: summary.avg_volume_ratio > 1.2 ? 'green' : 'gray',
    },
    {
      title: 'Active Signals',
      value: formatters.number(summary.active_signals),
      icon: BoltIcon,
      color: 'yellow',
    },
    {
      title: 'Last Updated',
      value: summary.last_updated,
      icon: ClockIcon,
      color: 'gray',
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => (
        <div key={card.title} className="bg-white overflow-hidden shadow rounded-lg">
          <div className="p-5">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <card.icon
                  className={`h-6 w-6 ${
                    card.color === 'blue' ? 'text-blue-400' :
                    card.color === 'green' ? 'text-green-400' :
                    card.color === 'yellow' ? 'text-yellow-400' :
                    'text-gray-400'
                  }`}
                />
              </div>
              <div className="ml-5 w-0 flex-1">
                <dl>
                  <dt className="text-sm font-medium text-gray-500 truncate">
                    {card.title}
                  </dt>
                  <dd className="text-lg font-medium text-gray-900">
                    {card.value}
                  </dd>
                </dl>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}






