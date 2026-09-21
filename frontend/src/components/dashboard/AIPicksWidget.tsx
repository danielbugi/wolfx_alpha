// File: frontend/src/components/dashboard/AIPicksWidget.tsx
import React from 'react';
import { AIPickItem, formatters } from '@/services/api';
import {
  SparklesIcon,
  FireIcon,
  ExclamationTriangleIcon,
  InformationCircleIcon
} from '@heroicons/react/20/solid';

interface AIPicksWidgetProps {
  picks: AIPickItem[];
}

export default function AIPicksWidget({ picks }: AIPicksWidgetProps) {
  if (!picks.length) {
    return (
      <div className="bg-gradient-to-r from-blue-500 to-purple-600 rounded-lg shadow-lg p-6">
        <div className="text-center text-white">
          <SparklesIcon className="h-12 w-12 mx-auto mb-4" />
          <h3 className="text-lg font-medium mb-2">AI-Enhanced Picks</h3>
          <p>No AI picks available at the moment</p>
        </div>
      </div>
    );
  }

  const getUrgencyIcon = (urgency: string) => {
    switch (urgency.toLowerCase()) {
      case 'immediate':
        return <FireIcon className="h-4 w-4 text-red-500" />;
      case 'very_high':
      case 'high':
        return <ExclamationTriangleIcon className="h-4 w-4 text-orange-500" />;
      default:
        return <InformationCircleIcon className="h-4 w-4 text-blue-500" />;
    }
  };

  return (
    <div className="bg-gradient-to-r from-blue-500 to-purple-600 rounded-lg shadow-lg overflow-hidden">
      <div className="px-6 py-4">
        <div className="flex items-center">
          <SparklesIcon className="h-6 w-6 text-white mr-2" />
          <h3 className="text-lg font-medium text-white">AI-Enhanced Stock Picks</h3>
          <div className="ml-auto">
            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-white text-blue-600">
              {picks.length} Active
            </span>
          </div>
        </div>
      </div>

      <div className="bg-white">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-300">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  Rank
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  Symbol
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wide">
                  ML Score
                </th>
                <th className="px-4 py-2 text-center text-xs font-medium text-gray-500 uppercase tracking-wide">
                  Urgency
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  Type
                </th>
                <th className="hidden lg:table-cell px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  Reasoning
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {picks.slice(0, 5).map((pick) => (
                <tr key={pick.symbol} className="hover:bg-gray-50">
                  <td className="px-4 py-3 whitespace-nowrap">
                    <div className="flex items-center">
                      <div className={`flex items-center justify-center h-6 w-6 rounded-full text-xs font-medium text-white ${
                        pick.rank <= 3 ? 'bg-yellow-500' : 'bg-gray-400'
                      }`}>
                        {pick.rank}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <div>
                      <div className="text-sm font-medium text-gray-900">{pick.symbol}</div>
                      <div className="text-sm text-gray-500">{formatters.currency(pick.current_price)}</div>
                    </div>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-right">
                    <div className={`text-sm font-medium ${
                      pick.ml_score == null ? 'text-gray-400' :
                      pick.ml_score >= 80 ? 'text-green-600' :
                      pick.ml_score >= 60 ? 'text-yellow-600' :
                      'text-gray-600'
                    }`}>
                      {pick.ml_score == null ? '—' : `${pick.ml_score.toFixed(0)}%`}
                    </div>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-center">
                    <div className="flex items-center justify-center">
                      {getUrgencyIcon(pick.urgency)}
                    </div>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900">
                    {pick.breakout_type}
                  </td>
                  <td className="hidden lg:table-cell px-4 py-3 text-sm text-gray-500 max-w-xs truncate">
                    {pick.reasoning}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}