'use client';

import React from 'react';
import { Card, CardBody } from '@nextui-org/react';
import { useOptionalAuth } from '@/contexts/AuthContext';
import type { StockDetail } from '@/services/api';
import OrderTicketCard from '@/components/stock/OrderTicketCard';

/**
 * The order ticket (entry, stop, targets, share count, copyable ticket) for OWNER accounts only; it renders nothing for
 * anyone else. On a stock with an active signal it starts from the signal's plan; on any other stock it starts from the
 * last price and a 2 ATR stop, and the levels are editable either way. This is a view-level gate, not access control: the API
 * still sends plans to every signed-in role. The product pages stay free of plans on purpose (see the content guard test),
 * which is why this lives in its own file outside that guard's list.
 */
export default function OwnerOrderTicket({ detail, price, atr }: { detail: StockDetail; price: number | null; atr: number | null }) {
  const auth = useOptionalAuth();
  if (auth?.user?.role !== 'owner') return null;
  const plan = detail.data_source === 'active_signal' ? (detail.ai_rating?.plan ?? null) : null;

  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <CardBody className="p-4 sm:p-5">
        <OrderTicketCard key={detail.symbol} symbol={detail.symbol} plan={plan} price={price} atr={atr} />
      </CardBody>
    </Card>
  );
}
