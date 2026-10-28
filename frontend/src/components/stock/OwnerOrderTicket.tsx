'use client';

import React from 'react';
import { Card, CardBody } from '@nextui-org/react';
import { useOptionalAuth } from '@/contexts/AuthContext';
import type { StockDetail } from '@/services/api';
import TradePlanCard from '@/components/stock/TradePlanCard';

/**
 * The stock's trade plan (entry, stop, targets, share count, copyable order ticket) for OWNER accounts only; for anyone
 * else, and when the stock has no active signal, it renders nothing. This is a view-level gate, not access control: the
 * API still sends the plan to every signed-in role. The product pages stay free of plans on purpose (see the content
 * guard test), which is why this lives in its own file outside that guard's list.
 */
export default function OwnerOrderTicket({ detail }: { detail: StockDetail }) {
  const auth = useOptionalAuth();
  if (auth?.user?.role !== 'owner') return null;
  if (detail.data_source !== 'active_signal' || !detail.ai_rating) return null;
  const plan = detail.ai_rating.plan;

  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <CardBody className="p-4 sm:p-5">
        {plan ? <TradePlanCard symbol={detail.symbol} plan={plan} /> : <p className="text-sm text-slate-400">No plan could be built for this signal.</p>}
      </CardBody>
    </Card>
  );
}
