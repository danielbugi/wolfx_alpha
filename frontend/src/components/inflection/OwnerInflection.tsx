'use client';

import React from 'react';
import { Card, CardBody } from '@nextui-org/react';
import { useOptionalAuth } from '@/contexts/AuthContext';
import InflectionPanel from '@/components/inflection/InflectionPanel';

/**
 * The fundamental-inflection section of the stock page, for OWNER accounts only (renders nothing for anyone else). A view-level gate, not access control: the API answers
 * every signed-in role. The product pages stay free of research signals on purpose (see the content guard test), so this lives in its own file outside that guard's list.
 */
export default function OwnerInflection({ symbol }: { symbol: string }) {
  const auth = useOptionalAuth();
  if (auth?.user?.role !== 'owner') return null;
  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <CardBody className="p-4 sm:p-5">
        <InflectionPanel key={symbol} symbol={symbol} />
      </CardBody>
    </Card>
  );
}
