// File: frontend/src/components/telegram/ComposeModal.tsx
// A brand-new post: text or photo, a target picker, the same wording guard / length limits / control token as edit, through the exact send
// path every script-based sender already uses (so the ledger records it exactly like any other post - see channel_control.compose's docstring).
'use client';

import React, { useEffect, useRef, useState } from 'react';
import { CheckCircleIcon, ExclamationTriangleIcon, PhotoIcon } from '@heroicons/react/24/outline';
import { telegramApi, toControlError } from '@/services/telegramApi';
import type { ChannelTarget, ComposeResult, ControlErrorInfo, KindCount, TargetOverview } from '@/services/telegramApi';
import { telegramTextLength } from '@/lib/telegramFormat';
import TelegramHtml from '@/lib/TelegramHtml';
import Dialog from '@/components/common/Dialog';
import { Btn } from '@/components/telegram/ui';

const MAX_TEXT = 4000;         // channel_control.compose's own cap: never silently split into more than one message
const MAX_CAPTION = 1024;
const MAX_UPLOAD_BYTES = 10_000_000;
const ALLOWED_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

interface Props {
  open: boolean;
  targets: TargetOverview[];
  kinds: KindCount[];
  token: string | null;
  controlsEnabled: boolean;
  onClose: () => void;
  onSent: () => void;
  /** The server said the token is wrong: the page forgets it. */
  onBadToken: () => void;
}

export default function ComposeModal({ open, targets, kinds, token, controlsEnabled, onClose, onSent, onBadToken }: Props) {
  const postable = targets.filter((t) => t.configured && !t.locked);
  const [target, setTarget] = useState<ChannelTarget | ''>('');
  const [kind, setKind] = useState('');
  const [contentType, setContentType] = useState<'text' | 'photo'>('text');
  const [text, setText] = useState('');
  const [caption, setCaption] = useState('');
  const [silent, setSilent] = useState(true);
  const [disablePreview, setDisablePreview] = useState(true);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoPreviewUrl, setPhotoPreviewUrl] = useState<string | null>(null);
  const [photoFileError, setPhotoFileError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<ControlErrorInfo | null>(null);
  const [result, setResult] = useState<ComposeResult | null>(null);

  useEffect(() => {
    if (!open) return;
    setTarget((postable[0]?.target as ChannelTarget) ?? '');
    setKind('');
    setContentType('text');
    setText('');
    setCaption('');
    setSilent(true);
    setDisablePreview(true);
    setPhotoFile(null);
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setPhotoFileError(null);
    setBusy(false);
    setActionError(null);
    setNetworkRiskAcknowledged(false);
    setResult(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-seed when the dialog actually (re)opens
  }, [open]);

  useEffect(() => () => {
    if (photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl);
  }, [photoPreviewUrl]);

  const pickPhoto = (file: File | null) => {
    setPhotoFileError(null);
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    if (!file) {
      setPhotoFile(null);
      return;
    }
    if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
      setPhotoFileError(`Unsupported file type (${file.type || 'unknown'}). Use PNG, JPEG or WEBP.`);
      setPhotoFile(null);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setPhotoFileError(`That image is ${(file.size / 1_000_000).toFixed(1)} MB; Telegram allows up to ${MAX_UPLOAD_BYTES / 1_000_000} MB.`);
      setPhotoFile(null);
      return;
    }
    setPhotoFile(file);
    setPhotoPreviewUrl(URL.createObjectURL(file));
  };

  const body = contentType === 'text' ? text : caption;
  const bodyLength = telegramTextLength(body);
  const limit = contentType === 'text' ? MAX_TEXT : MAX_CAPTION;
  const over = bodyLength > limit;
  const empty = contentType === 'text' ? text.trim() === '' : false;    // an empty caption is valid for a photo
  const canAct = controlsEnabled && token !== null;
  // A request that timed out may already have reached Telegram - sending is NOT idempotent (unlike edit/delete/pin), so resubmitting the
  // identical content would risk a genuine duplicate post. Once that happens, the normal Send button is replaced by an explicit "I checked
  // the channel" confirmation instead of just becoming clickable again.
  const [networkRiskAcknowledged, setNetworkRiskAcknowledged] = useState(false);
  const timedOut = actionError?.code === 'network';
  const canSubmit = canAct && !!target && !over && !empty && (contentType === 'text' || photoFile !== null) && (!timedOut || networkRiskAcknowledged);
  const hasUnsavedDraft = !result && (contentType === 'text' ? text.trim() !== '' : caption.trim() !== '' || photoFile !== null);

  const send = async (acknowledgeWording: boolean) => {
    if (!token || !target) return;
    setBusy(true);
    setActionError(null);
    setNetworkRiskAcknowledged(false);
    try {
      const r =
        contentType === 'text'
          ? await telegramApi.composeText(target, text, kind.trim() || null, silent, disablePreview, acknowledgeWording, token)
          : await telegramApi.composePhoto(target, photoFile as File, caption, kind.trim() || null, silent, acknowledgeWording, token);
      setResult(r);
      onSent();
    } catch (err) {
      const e = toControlError(err);
      if (e.code === 'bad_token') onBadToken();
      if (e.code === 'network') {
        setActionError({
          code: e.code,
          message: 'The request did not finish, so this post may or may not have already reached Telegram. Check the channel before sending it again.',
          warnings: [],
        });
      } else {
        setActionError(e);
      }
    } finally {
      setBusy(false);
    }
  };

  const startAnother = () => {
    setResult(null);
    setText('');
    setCaption('');
    setPhotoFile(null);
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    if (fileInputRef.current) fileInputRef.current.value = '';
    setActionError(null);
  };

  return (
    <Dialog open={open} onClose={onClose} labelledBy="compose-dialog-title" dismissable={!busy && !hasUnsavedDraft}>
      <header className="border-b border-slate-200 px-6 py-4">
        <h2 id="compose-dialog-title" className="text-base font-semibold text-slate-800">New post</h2>
        <p className="mt-0.5 text-xs text-slate-500">Composed here, sent the same way every scheduled post is — recorded, previewed and refusable by the same rules.</p>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {!canAct && (
          <p className="mb-3 flex items-center gap-2 rounded-md bg-amber-50 p-2 text-sm text-amber-900" role="status">
            <ExclamationTriangleIcon className="h-4 w-4 shrink-0" />
            {controlsEnabled ? 'Enter the control token at the top of the page to send.' : 'Sending is switched off on the server (no TELEGRAM_CONTROL_TOKEN).'}
          </p>
        )}

        {result ? (
          <div className="space-y-3">
            <p className="flex items-center gap-2 rounded-md bg-emerald-50 p-2 text-sm text-emerald-800" role="status" aria-live="polite">
              <CheckCircleIcon className="h-4 w-4 shrink-0" /> Sent to the {result.target === 'prod' ? 'production' : 'dev'} channel (Telegram message #{result.message_id}).
            </p>
            {result.message === null && (
              <p className="text-xs text-slate-500">It reached Telegram, but has not shown up in the list yet — refresh the page in a moment to see it there.</p>
            )}
            <div className="flex justify-end gap-2">
              <Btn onClick={startAnother}>Send another</Btn>
              <Btn variant="primary" onClick={onClose}>Done</Btn>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-xs font-medium text-slate-600">
                Channel
                <select
                  value={target}
                  onChange={(e) => setTarget(e.target.value as ChannelTarget)}
                  disabled={busy}
                  className="mt-1 w-full rounded-md border border-slate-300 bg-white p-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-300"
                >
                  <option value="" disabled>Choose a channel…</option>
                  {postable.map((t) => (
                    <option key={t.target} value={t.target}>{t.label}</option>
                  ))}
                </select>
                {postable.length === 0 && <span className="mt-1 block text-red-700">No channel is both configured and unlocked right now.</span>}
              </label>
              <label className="block text-xs font-medium text-slate-600">
                Kind (optional label for the list)
                <input
                  list="compose-kind-options"
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
                  disabled={busy}
                  placeholder="manual"
                  maxLength={30}
                  className="mt-1 w-full rounded-md border border-slate-300 bg-white p-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-300"
                />
                <datalist id="compose-kind-options">
                  {kinds.map((k) => (
                    <option key={k.kind} value={k.kind}>{k.label}</option>
                  ))}
                </datalist>
              </label>
            </div>

            <div className="flex gap-2">
              <Btn variant={contentType === 'text' ? 'primary' : 'secondary'} size="sm" disabled={busy} onClick={() => setContentType('text')}>Text</Btn>
              <Btn variant={contentType === 'photo' ? 'primary' : 'secondary'} size="sm" disabled={busy} onClick={() => setContentType('photo')}>Photo</Btn>
            </div>

            {contentType === 'photo' && (
              <>
                <label htmlFor="compose-photo-file" className="block text-xs font-medium text-slate-600">
                  Image (PNG, JPEG or WEBP, up to {MAX_UPLOAD_BYTES / 1_000_000} MB)
                </label>
                <input
                  ref={fileInputRef}
                  id="compose-photo-file"
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  disabled={busy}
                  onChange={(e) => pickPhoto(e.target.files?.[0] ?? null)}
                  className="block w-full text-xs text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-700 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-white hover:file:bg-slate-800"
                />
                {photoFileError && <p className="text-xs text-red-700" role="alert">{photoFileError}</p>}
              </>
            )}

            <label htmlFor="compose-body" className="block text-xs font-medium text-slate-600">
              {contentType === 'text' ? 'Text (Telegram HTML)' : 'Caption (Telegram HTML, optional)'}
            </label>
            <textarea
              id="compose-body"
              value={body}
              onChange={(e) => (contentType === 'text' ? setText(e.target.value) : setCaption(e.target.value))}
              rows={contentType === 'text' ? 10 : 5}
              spellCheck={false}
              disabled={busy}
              className="w-full rounded-md border border-slate-300 bg-white p-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-slate-300"
            />
            <span className={over ? 'block text-xs font-semibold text-red-700' : 'block text-xs text-slate-500'} role={over ? 'alert' : undefined}>
              {bodyLength} / {limit} characters{over ? ' — too long' : ''}
            </span>

            <div className="flex flex-wrap gap-4 text-xs text-slate-600">
              <label className="flex items-center gap-1.5">
                <input type="checkbox" checked={silent} onChange={(e) => setSilent(e.target.checked)} disabled={busy} /> Silent (no notification sound)
              </label>
              {contentType === 'text' && (
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={disablePreview} onChange={(e) => setDisablePreview(e.target.checked)} disabled={busy} /> No link preview card
                </label>
              )}
            </div>

            <p className="text-xs font-medium text-slate-600">Preview</p>
            <div className="rounded-lg border border-slate-200 bg-slate-100 p-3">
              {contentType === 'photo' &&
                (photoPreviewUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element -- a local object: URL preview of the chosen file, never a remote image
                  <img src={photoPreviewUrl} alt="Image preview" className="mb-2 max-h-48 w-full rounded object-contain" />
                ) : (
                  <div className="mb-2 flex h-24 items-center justify-center gap-2 rounded bg-slate-200 text-xs text-slate-500">
                    <PhotoIcon className="h-5 w-5" aria-hidden /> Choose an image above to preview it here.
                  </div>
                ))}
              <div className="rounded-lg bg-white p-3 shadow-sm">
                {body.trim() ? <TelegramHtml html={body} /> : <p className="text-sm text-slate-400">Nothing typed yet.</p>}
              </div>
            </div>

            {actionError && actionError.code === 'wording' ? (
              <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-900" role="alert">
                <p className="font-medium">The wording guard stops this post.</p>
                <p className="mt-1">
                  Channel posts do not use advice-style words, and this adds: <span className="font-semibold">{actionError.warnings.join(', ')}</span>.
                </p>
                <Btn variant="warning" size="sm" className="mt-2" disabled={busy || !canAct} onClick={() => send(true)}>
                  I have checked it — send anyway
                </Btn>
              </div>
            ) : timedOut ? (
              <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-900" role="alert">
                <p className="font-medium">The request did not finish.</p>
                <p className="mt-1">{actionError.message}</p>
                <label className="mt-2 flex items-center gap-1.5">
                  <input type="checkbox" checked={networkRiskAcknowledged} onChange={(e) => setNetworkRiskAcknowledged(e.target.checked)} />
                  I checked the channel and want to send this anyway
                </label>
              </div>
            ) : (
              actionError && (
                <p className="flex items-start gap-2 rounded-md bg-red-50 p-3 text-sm text-red-800" role="alert">
                  <ExclamationTriangleIcon className="mt-0.5 h-4 w-4 shrink-0" /> {actionError.message}
                </p>
              )
            )}
          </div>
        )}
      </div>

      {!result && (
        <footer className="flex justify-end gap-2 border-t border-slate-200 px-6 py-3">
          <Btn disabled={busy} onClick={onClose}>Cancel</Btn>
          <Btn variant="primary" disabled={busy || !canSubmit} onClick={() => send(false)}>
            {busy ? 'Sending…' : 'Send to Telegram'}
          </Btn>
        </footer>
      )}
    </Dialog>
  );
}
