
// File: frontend/src/components/dashboard/TopGainersTable.tsx
import React from 'react';
import { StockItem, formatters } from '@/services/api';
import { ArrowUpIcon } from '@heroicons/react/20/solid';

interface TopGainersTableProps {
  gainers: StockItem[];
}

export default function TopGainersTable({ gainers }: TopGainersTableProps) {
  if (!gainers.length) {
    return <div className="text-center text-gray-500 py-4">No gainers data available</div>;
  }

  return (
    <div className="overflow-hidden">
      <table className="min-w-full divide-y divide-gray-300">
        <thead>
          <tr>
            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
              Symbol
            </th>
            <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wide">
              Price
            </th>
            <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wide">
              Change
            </th>
            <th className="hidden sm:table-cell px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
              Sector
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200">
          {gainers.map((stock) => (
            <tr key={stock.symbol} className="hover:bg-gray-50">
              <td className="px-3 py-2 whitespace-nowrap">
                <div className="flex items-center">
                  <div className="text-sm font-medium text-gray-900">
                    {stock.symbol}
                  </div>
                </div>
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-right text-sm text-gray-900">
                {formatters.currency(stock.current_price)}
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-right">
                <div className="flex items-center justify-end">
                  <ArrowUpIcon className="h-4 w-4 text-green-500 mr-1" />
                  <span className="text-sm font-medium text-green-600">
                    {formatters.percentage(stock.price_change_pct)}
                  </span>
                </div>
              </td>
              <td className="hidden sm:table-cell px-3 py-2 whitespace-nowrap text-sm text-gray-500">
                {stock.sector}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}