// File: frontend/src/components/telegram/MessageModal.tsx
// One message: what it looks like in Telegram, its facts and history, and (with the control token) edit and delete. The server decides
// whether an action is allowed (production lock, 48 h delete window, ...) and says why; this component only shows that answer.
import React, { useEffect, useRef, useState } from 'react';
import { Spinner } from '@nextui-org/react';
import { CheckCircleIcon, ExclamationTriangleIcon, PhotoIcon } from '@heroicons/react/24/outline';
import { telegramApi, toControlError } from '@/services/telegramApi';
import type { ActionResult, ControlErrorInfo, MessageDetail } from '@/services/telegramApi';
import { formatFull, formatWhen, telegramTextLength, timeUntil } from '@/lib/telegramFormat';
import TelegramHtml from '@/lib/TelegramHtml';
import Dialog from '@/components/common/Dialog';
import { ChannelChip, StatusChip } from '@/components/telegram/StatusChips';
import { Btn } from '@/components/telegram/ui';
import type { RowMode } from '@/components/telegram/MessageTable';

/** 'photo' is local to this component only (replacing the image of an existing photo message) - it never comes from or goes back to the
 * table's own RowMode, which only knows about view/edit/delete. */
type LocalMode = RowMode | 'photo';

interface Props {
  id: number | null;
  initialMode: RowMode;
  timezone: string;
  token: string | null;
  controlsEnabled: boolean;
  onClose: () => void;
  /** Called after a change reached Telegram, so the list and the counts refresh. */
  onChanged: () => void;
  /** The server said the token is wrong: the page forgets it. */
  onBadToken: () => void;
}

const MAX_UPLOAD_BYTES = 10_000_000;
const ALLOWED_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

const OUTCOME_LABEL: Record<string, string> = {
  done: 'done',
  unchanged: 'no change',
  gone: 'already gone',
  refused: 'refused by Telegram',
  failed: 'failed',
};

function Bubble({ detail, html }: { detail: MessageDetail; html: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-100 p-3">
      {detail.content_type === 'photo' && (
        <div className="mb-2 flex h-24 items-center justify-center gap-2 rounded bg-slate-200 text-xs text-slate-500">
          <PhotoIcon className="h-5 w-5" aria-hidden />
          Photo message. The image is not stored; only its caption can be edited.
        </div>
      )}
      <div className="rounded-lg bg-white p-3 shadow-sm">
        <TelegramHtml html={html} />
        {detail.buttons.length > 0 && (
          <div className="mt-3 space-y-1">
            {detail.buttons.map((line, i) => (
              <div key={i} className="flex gap-1">
                {line.map((b, j) => (
                  <span key={j} title={b.url ?? undefined} className="flex-1 rounded bg-sky-50 px-2 py-1 text-center text-xs font-medium text-sky-800">
                    {b.text ?? '—'}
                  </span>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1.5 text-sm">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right font-medium text-slate-700">{children}</dd>
    </div>
  );
}

export default function MessageModal({ id, initialMode, timezone, token, controlsEnabled, onClose, onChanged, onBadToken }: Props) {
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [loadError, setLoadError] = useState<ControlErrorInfo | null>(null);
  const [mode, setMode] = useState<LocalMode>('view');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<ControlErrorInfo | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [openedAt, setOpenedAt] = useState(() => Date.now());

  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoPreviewUrl, setPhotoPreviewUrl] = useState<string | null>(null);
  const [photoCaption, setPhotoCaption] = useState('');
  const [photoFileError, setPhotoFileError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setDetail(null);
    setLoadError(null);
    setActionError(null);
    setNotice(null);
    setPhotoFile(null);
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setPhotoFileError(null);
    if (id == null) return;
    let cancelled = false;
    setOpenedAt(Date.now());
    telegramApi
      .getMessage(id)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setDraft(d.text);
        setPhotoCaption(d.text);
        setMode(initialMode);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(toControlError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [id, initialMode]);

  useEffect(() => () => {
    if (photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl);
  }, [photoPreviewUrl]);

  const canAct = controlsEnabled && token !== null;
  const hasUnsavedDraft =
    (mode === 'edit' && detail !== null && draft !== detail.text) || (mode === 'photo' && (photoFile !== null || (detail !== null && photoCaption !== detail.text)));
  const draftLength = telegramTextLength(draft);
  const over = detail !== null && draftLength > detail.limit;
  const unchanged = detail !== null && draft === detail.text;
  const empty = draft.trim() === '';
  const photoCaptionLength = telegramTextLength(photoCaption);
  const photoCaptionOver = photoCaptionLength > 1024;

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

  const run = async (call: (t: string) => Promise<ActionResult>, doneText: (r: ActionResult) => string) => {
    if (!token) return;
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const r = await call(token);
      setDetail(r.message);
      setDraft(r.message.text);
      setMode('view');
      setNotice(doneText(r));
      onChanged();
    } catch (err) {
      const e = toControlError(err);
      if (e.code === 'bad_token') onBadToken();
      if (e.code === 'network' && detail) {
        // The request did not finish (timeout / dropped connection): the server may already have changed Telegram. Say so, and re-read the truth.
        setActionError({
          code: e.code,
          message: 'The request did not finish, so the change may or may not have reached Telegram. The record below was re-read; check it before trying again.',
          warnings: [],
        });
        onChanged();
        telegramApi.getMessage(detail.id).then((d) => setDetail(d)).catch(() => undefined);
      } else {
        setActionError(e);
      }
    } finally {
      setBusy(false);
    }
  };

  const save = (acknowledgeWording: boolean) =>
    detail &&
    run(
      (t) => telegramApi.editMessage(detail.id, draft, acknowledgeWording, t),
      (r) =>
        r.result === 'edited'
          ? 'Saved. The channel now shows the new text.'
          : r.result === 'unchanged'
            ? 'Telegram already showed this text, so nothing changed; the record was brought in line.'
            : 'Telegram says this message no longer exists; it is marked deleted here.',
    );

  const remove = () =>
    detail &&
    run(
      (t) => telegramApi.deleteMessage(detail.id, t),
      (r) => (r.result === 'deleted' ? 'Deleted from the channel.' : 'It was already gone from the channel; marked deleted here.'),
    );

  const togglePin = () =>
    detail &&
    run(
      (t) => (detail.pinned ? telegramApi.unpinMessage(detail.id, t) : telegramApi.pinMessage(detail.id, t)),
      (r) =>
        detail.pinned
          ? r.result === 'unpinned'
            ? 'Unpinned.'
            : 'It was already not pinned; the record was brought in line.'
          : 'Pinned in the channel.',
    );

  const savePhoto = (acknowledgeWording: boolean) =>
    detail &&
    photoFile &&
    run(
      (t) => telegramApi.replacePhoto(detail.id, photoFile, photoCaption, acknowledgeWording, t),
      (r) =>
        r.result === 'edited'
          ? 'Saved. The channel now shows the new image.'
          : r.result === 'unchanged'
            ? 'Telegram already showed this image and caption, so nothing changed; the record was brought in line.'
            : 'Telegram says this message no longer exists; it is marked deleted here.',
    );

  const untilLabel = detail ? timeUntil(detail.deletable_until, openedAt) : null;
  const lockedHint = !controlsEnabled
    ? 'This action is switched off on the server (no TELEGRAM_CONTROL_TOKEN).'
    : token === null
      ? 'Enter the control token at the top of the page to save changes.'
      : null;
  const backToView = () => {
    setActionError(null);
    setMode('view');
  };

  return (
    <Dialog open={id !== null} onClose={onClose} labelledBy="message-dialog-title" dismissable={!busy && !hasUnsavedDraft}>
      <header className="border-b border-slate-200 px-6 py-4">
        <h2 id="message-dialog-title" className="text-base font-semibold text-slate-800">
          {detail ? detail.kind_label : 'Message'}
        </h2>
        {detail && (
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <ChannelChip target={detail.target} />
            <StatusChip message={detail} />
            <span>Telegram message #{detail.message_id}</span>
            <span>· posted {formatWhen(detail.sent_at, timezone)}</span>
          </div>
        )}
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loadError && (
          <p className="flex items-center gap-2 text-sm text-red-700" role="alert">
            <ExclamationTriangleIcon className="h-4 w-4 shrink-0" /> {loadError.message}
          </p>
        )}
        {!detail && !loadError && (
          <div className="flex justify-center py-12"><Spinner color="primary" /></div>
        )}

        {detail && (
          <div className="grid grid-cols-1 gap-5 md:grid-cols-5">
            <div className="space-y-3 md:col-span-3">
              {notice && (
                <p className="flex items-center gap-2 rounded-md bg-emerald-50 p-2 text-sm text-emerald-800" role="status" aria-live="polite">
                  <CheckCircleIcon className="h-4 w-4 shrink-0" /> {notice}
                </p>
              )}

              {mode === 'edit' ? (
                <>
                  <label htmlFor="message-draft" className="block text-xs font-medium text-slate-600">
                    {detail.content_type === 'photo' ? 'Caption (Telegram HTML)' : 'Text (Telegram HTML)'}
                  </label>
                  <textarea
                    id="message-draft"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={10}
                    spellCheck={false}
                    disabled={busy}
                    className="w-full rounded-md border border-slate-300 bg-white p-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-slate-300"
                  />
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className={over ? 'font-semibold text-red-700' : 'text-slate-500'} role={over ? 'alert' : undefined}>
                      {draftLength} / {detail.limit} characters{over ? ' — too long for Telegram' : ''}
                    </span>
                    {detail.edit_count > 0 && (
                      <Btn variant="ghost" size="sm" disabled={busy || draft === detail.original_text} onClick={() => setDraft(detail.original_text)}>
                        Restore the original text
                      </Btn>
                    )}
                  </div>
                  <p className="text-xs font-medium text-slate-600">Preview</p>
                  <Bubble detail={detail} html={draft} />
                </>
              ) : mode === 'photo' ? (
                <>
                  <label htmlFor="photo-file" className="block text-xs font-medium text-slate-600">
                    New image (PNG, JPEG or WEBP, up to {MAX_UPLOAD_BYTES / 1_000_000} MB)
                  </label>
                  <input
                    ref={fileInputRef}
                    id="photo-file"
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    disabled={busy}
                    onChange={(e) => pickPhoto(e.target.files?.[0] ?? null)}
                    className="block w-full text-xs text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-700 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-white hover:file:bg-slate-800"
                  />
                  {photoFileError && (
                    <p className="text-xs text-red-700" role="alert">{photoFileError}</p>
                  )}
                  <label htmlFor="photo-caption" className="block text-xs font-medium text-slate-600">Caption (Telegram HTML)</label>
                  <textarea
                    id="photo-caption"
                    value={photoCaption}
                    onChange={(e) => setPhotoCaption(e.target.value)}
                    rows={5}
                    spellCheck={false}
                    disabled={busy}
                    className="w-full rounded-md border border-slate-300 bg-white p-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-slate-300"
                  />
                  <span className={photoCaptionOver ? 'block text-xs font-semibold text-red-700' : 'block text-xs text-slate-500'} role={photoCaptionOver ? 'alert' : undefined}>
                    {photoCaptionLength} / 1024 characters{photoCaptionOver ? ' — too long for Telegram' : ''}
                  </span>
                  <p className="text-xs font-medium text-slate-600">Preview</p>
                  <div className="rounded-lg border border-slate-200 bg-slate-100 p-3">
                    {photoPreviewUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element -- a local object: URL preview of the chosen file, never a remote image
                      <img src={photoPreviewUrl} alt="New image preview" className="mb-2 max-h-48 w-full rounded object-contain" />
                    ) : (
                      <div className="mb-2 flex h-24 items-center justify-center gap-2 rounded bg-slate-200 text-xs text-slate-500">
                        <PhotoIcon className="h-5 w-5" aria-hidden /> Choose an image above to preview it here.
                      </div>
                    )}
                    <div className="rounded-lg bg-white p-3 shadow-sm"><TelegramHtml html={photoCaption} /></div>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-xs font-medium text-slate-600">
                    {detail.status === 'deleted' ? 'As it was before it was deleted' : 'As it appears in the channel'}
                  </p>
                  <Bubble detail={detail} html={detail.text} />
                  {mode === 'delete' && (
                    <p className="rounded-md bg-red-50 p-3 text-sm text-red-800" role="alert">
                      This removes the message from the {detail.target === 'prod' ? 'production' : 'dev'} channel for everyone. It cannot be
                      restored; a new post would be a new message.
                    </p>
                  )}
                  {detail.edit_count > 0 && (
                    <details className="text-sm">
                      <summary className="cursor-pointer text-xs font-medium text-slate-600">Original text, as first sent</summary>
                      <div className="mt-2"><Bubble detail={detail} html={detail.original_text} /></div>
                    </details>
                  )}
                </>
              )}

              {actionError && actionError.code === 'wording' ? (
                <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-900" role="alert">
                  <p className="font-medium">The wording guard stops this.</p>
                  <p className="mt-1">
                    Channel posts do not use advice-style words, and this adds: <span className="font-semibold">{actionError.warnings.join(', ')}</span>.
                  </p>
                  <Btn variant="warning" size="sm" className="mt-2" disabled={busy || !canAct} onClick={() => (mode === 'photo' ? savePhoto(true) : save(true))}>
                    I have checked it — save anyway
                  </Btn>
                </div>
              ) : (
                actionError && (
                  <p className="flex items-start gap-2 rounded-md bg-red-50 p-3 text-sm text-red-800" role="alert">
                    <ExclamationTriangleIcon className="mt-0.5 h-4 w-4 shrink-0" /> {actionError.message}
                  </p>
                )
              )}
            </div>

            <div className="space-y-4 md:col-span-2">
              <dl className="divide-y divide-slate-100 rounded-lg border border-slate-200 px-3">
                <Fact label="Posted">{formatFull(detail.sent_at, timezone)}</Fact>
                <Fact label="Last edited">{formatFull(detail.edited_at, timezone)}</Fact>
                {detail.deleted_at && <Fact label="Deleted">{formatFull(detail.deleted_at, timezone)}</Fact>}
                <Fact label="Length">{detail.text_length} / {detail.limit}</Fact>
                <Fact label="Notification">{detail.silent ? 'Silent' : 'With sound'}</Fact>
                <Fact label="Pinned by First Light">{detail.pinned ? 'Yes' : 'No'}</Fact>
                <Fact label="Delete window">
                  {detail.status === 'deleted' ? '—' : untilLabel ? `open ${untilLabel}` : 'closed (over 48 h)'}
                </Fact>
              </dl>

              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">History from this page</h3>
                {detail.audit.length === 0 ? (
                  <p className="text-xs text-slate-500">Nothing has been edited or deleted from here.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {detail.audit.map((a, i) => (
                      <li key={i} className="text-xs text-slate-600">
                        <span className="tabular-nums text-slate-400">{formatWhen(a.at, timezone)}</span>{' '}
                        <span className="font-medium capitalize">{a.action}</span> — {OUTCOME_LABEL[a.outcome] ?? a.outcome}
                        {a.detail && <span className="block text-slate-400">{a.detail}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      <footer className="flex flex-col gap-2 border-t border-slate-200 px-6 py-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-slate-500">
          {detail && mode === 'view' && !detail.can_edit.ok && detail.can_edit.reason}
          {detail && mode === 'view' && detail.can_edit.ok && !detail.can_pin.ok && detail.can_pin.reason}
          {detail && mode === 'view' && detail.can_edit.ok && detail.can_pin.ok && !detail.can_delete.ok && detail.can_delete.reason}
          {detail && mode !== 'view' && lockedHint}
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          {mode === 'view' && (
            <>
              <Btn onClick={onClose}>Close</Btn>
              <Btn
                disabled={!detail?.can_pin.ok}
                onClick={() => {
                  setActionError(null);
                  setNotice(null);
                  togglePin();
                }}
              >
                {detail?.pinned ? 'Unpin' : 'Pin'}
              </Btn>
              {detail?.content_type === 'photo' && (
                <Btn
                  disabled={!detail?.can_edit.ok}
                  onClick={() => {
                    setActionError(null);
                    setNotice(null);
                    setMode('photo');
                  }}
                >
                  Replace photo
                </Btn>
              )}
              <Btn
                disabled={!detail?.can_edit.ok}
                onClick={() => {
                  setActionError(null);
                  setNotice(null);
                  setMode('edit');
                }}
              >
                Edit {detail?.content_type === 'photo' ? 'caption' : ''}
              </Btn>
              <Btn
                variant="danger"
                disabled={!detail?.can_delete.ok}
                onClick={() => {
                  setActionError(null);
                  setNotice(null);
                  setMode('delete');
                }}
              >
                Delete
              </Btn>
            </>
          )}
          {mode === 'edit' && detail && (
            <>
              <Btn
                disabled={busy}
                onClick={() => {
                  setDraft(detail.text);
                  backToView();
                }}
              >
                Cancel
              </Btn>
              <Btn variant="primary" disabled={busy || !canAct || over || unchanged || empty} onClick={() => save(false)}>
                {busy ? 'Saving…' : 'Save to Telegram'}
              </Btn>
            </>
          )}
          {mode === 'photo' && detail && (
            <>
              <Btn
                disabled={busy}
                onClick={() => {
                  pickPhoto(null);
                  setPhotoCaption(detail.text);
                  if (fileInputRef.current) fileInputRef.current.value = '';
                  backToView();
                }}
              >
                Cancel
              </Btn>
              <Btn
                variant="primary"
                disabled={busy || !canAct || !photoFile || photoCaptionOver}
                onClick={() => savePhoto(false)}
              >
                {busy ? 'Uploading…' : 'Replace on Telegram'}
              </Btn>
            </>
          )}
          {mode === 'delete' && (
            <>
              <Btn disabled={busy} onClick={backToView}>Keep it</Btn>
              <Btn variant="danger" disabled={busy || !canAct} onClick={remove}>
                {busy ? 'Deleting…' : 'Delete from Telegram'}
              </Btn>
            </>
          )}
        </div>
      </footer>
    </Dialog>
  );
}
