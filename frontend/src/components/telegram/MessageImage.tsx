// File: frontend/src/components/telegram/MessageImage.tsx
// The picture of a photo message that is in the channel. The server fetches it from Telegram (the ledger never stored pictures) and caches it;
// the request needs the sign-in header, so it is loaded as a blob rather than through a plain <img src>.
'use client';

import React, { useEffect, useState } from 'react';
import { PhotoIcon } from '@heroicons/react/24/outline';
import { telegramApi, toControlError } from '@/services/telegramApi';
import LoadingSpinner from '@/components/common/LoadingSpinner';

type State = { status: 'loading' } | { status: 'ready'; url: string } | { status: 'error'; message: string };

export default function MessageImage({ id, alt }: { id: number; alt: string }) {
  const [state, setState] = useState<State>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ status: 'loading' });
    telegramApi
      .getImage(id)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ status: 'ready', url: objectUrl });
      })
      .catch(async (err) => {
        if (cancelled) return;
        setState({ status: 'error', message: toControlError(err).message });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id]);

  if (state.status === 'ready') {
    // eslint-disable-next-line @next/next/no-img-element -- a blob URL of a just-fetched picture; next/image cannot optimise it
    return <img src={state.url} alt={alt} className="mb-2 max-h-80 w-full rounded object-contain bg-slate-200" data-testid="message-image" />;
  }
  return (
    <div className="mb-2 flex h-28 items-center justify-center gap-2 rounded bg-slate-200 px-3 text-center text-xs text-slate-500" data-testid="message-image-placeholder">
      {state.status === 'loading' ? (
        <>
          <LoadingSpinner size="small" /> Loading the picture from Telegram…
        </>
      ) : (
        <>
          <PhotoIcon className="h-5 w-5 shrink-0" aria-hidden /> {state.message}
        </>
      )}
    </div>
  );
}
