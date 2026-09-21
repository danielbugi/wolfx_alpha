// File: frontend/src/components/dashboard/UnusualVolumeTable.tsx
import React from 'react';
import { StockItem, formatters } from '@/services/api';
import { SpeakerWaveIcon } from '@heroicons/react/20/solid';

interface UnusualVolumeTableProps {
  stocks: StockItem[];
}

export default function UnusualVolumeTable({ stocks }: UnusualVolumeTableProps) {
  if (!stocks.length) {
    return <div className="text-center text-gray-500 py-4">No unusual volume data available</div>;
  }

  return (
    <div className="overflow-x-auto">
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
              Volume
            </th>
            <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wide">
              Volume Ratio
            </th>
            <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wide">
              Change
            </th>
            <th className="hidden md:table-cell px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
              Sector
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200">
          {stocks.map((stock) => (
            <tr key={stock.symbol} className="hover:bg-gray-50">
              <td className="px-3 py-2 whitespace-nowrap">
                <div className="flex items-center">
                  <SpeakerWaveIcon className="h-4 w-4 text-orange-500 mr-2" />
                  <div className="text-sm font-medium text-gray-900">
                    {stock.symbol}
                  </div>
                </div>
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-right text-sm text-gray-900">
                {formatters.currency(stock.current_price)}
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-right text-sm text-gray-900">
                {formatters.compact(stock.volume)}
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-right">
                <span className={`text-sm font-medium ${
                  stock.volume_ratio >= 3 ? 'text-red-600' :
                  stock.volume_ratio >= 2 ? 'text-orange-600' :
                  'text-gray-600'
                }`}>
                  {stock.volume_ratio.toFixed(1)}x
                </span>
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-right">
                <span className={`text-sm font-medium ${
                  stock.price_change_pct >= 0 ? 'text-green-600' : 'text-red-600'
                }`}>
                  {formatters.percentage(stock.price_change_pct)}
                </span>
              </td>
              <td className="hidden md:table-cell px-3 py-2 whitespace-nowrap text-sm text-gray-500">
                {stock.sector}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}