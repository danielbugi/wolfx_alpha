// The Telegram Control Center's three fixes (2026-10-27): the right reason when a Collaborator cannot edit (not a blamed token), the pictures of photo
// messages, and "what's live now" (current vs old format, switched-off post types, one-click removal of old posts).
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { LiveStatus, MessageDetail } from '@/services/telegramApi';

const api = vi.hoisted(() => ({
  getLive: vi.fn(),
  getImage: vi.fn(),
  deleteOld: vi.fn(),
  getMessage: vi.fn(),
}));
vi.mock('@/services/telegramApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/telegramApi')>();
  return { ...actual, telegramApi: { ...actual.telegramApi, ...api } };
});

import LiveNowPanel from '@/components/telegram/LiveNowPanel';
import MessageImage from '@/components/telegram/MessageImage';
import MessageModal from '@/components/telegram/MessageModal';
import MessageFilters, { DEFAULT_FILTERS } from '@/components/telegram/MessageFilters';

const live = (over: Partial<LiveStatus> = {}): LiveStatus => ({
  target: 'prod',
  as_of: '2026-10-28T12:00:00+00:00',
  timezone: 'UTC',
  format_changed_on: '2026-10-26',
  current: [
    { key: 'brief', label: 'Daily market brief (card + text)', cadence: 'Once after each US market close, about 22:00 UTC', last_sent_at: '2026-10-27T22:01:00+00:00', status: 'posted', live_posts: 4 },
    { key: 'assistant', label: 'Assistant post (with picture)', cadence: 'Every day at 18:00 UTC', last_sent_at: '2026-10-25T18:00:00+00:00', status: 'check', live_posts: 6 },
  ],
  paused: [
    { key: 'old_format', label: 'Old five-post market format', reason: 'Replaced by the daily market brief on 2026-10-26.', restore: 'Set POST_MARKET_FORMAT=package', last_sent_at: '2026-10-23T21:35:00+00:00', live_posts: 30, resumed: false },
    { key: 'earnings_post', label: "Separate 'who reports today' post", reason: 'Paused 2026-10-22.', restore: 'Re-enable the earnings timer', last_sent_at: '2026-10-27T07:00:00+00:00', live_posts: 1, resumed: true },
  ],
  old_posts: { live: 31, removable_now: 4, too_old_for_a_bot: 26, pinned: 1, by_kind: [{ kind: 'digest_list', label: 'Daily digest - list', live: 12 }] },
  ...over,
});

const panelProps = { target: 'prod' as const, timezone: 'UTC', isOwner: true, token: 'tok', controlsEnabled: true, onChanged: vi.fn(), onBadToken: vi.fn() };

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  panelProps.onChanged.mockReset();
  panelProps.onBadToken.mockReset();
  api.getLive.mockResolvedValue(live());
});

describe("What's live now", () => {
  it('shows what posts now, what is switched off, and flags a switched-off type that started posting again', async () => {
    render(<LiveNowPanel {...panelProps} />);
    expect(await screen.findByTestId('live-current-brief')).toBeTruthy();
    expect(screen.getByTestId('live-current-brief').textContent).toContain('Posting');
    expect(screen.getByTestId('live-current-assistant').textContent).toContain('Quiet longer than usual');
    expect(screen.getByTestId('live-paused-old_format').textContent).toContain('Off');
    expect(screen.getByTestId('live-paused-old_format').textContent).toContain('Set POST_MARKET_FORMAT=package');
    expect(screen.getByTestId('live-paused-earnings_post').textContent).toContain('Posting again');
    expect(screen.getByTestId('live-now').textContent).toContain('1 switched-off post type is posting again');
    expect(screen.getByTestId('live-old-summary').textContent).toContain('31 from before 2026-10-26');
    expect(screen.getByTestId('live-old-summary').textContent).toContain('4 can still be removed');
    expect(screen.getByTestId('live-old-summary').textContent).toContain('26 are older');
    expect(screen.getByTestId('live-old-summary').textContent).toContain('1 pinned');
    expect(screen.queryByText(/overdue/i)).toBeNull();                       // the old Windows-era schedule called everything overdue
  });

  it('removes old-format posts only after an explicit confirmation, in rounds, with the control token', async () => {
    api.deleteOld.mockResolvedValueOnce({ deleted: 3, remaining: 1, failed: 0 }).mockResolvedValueOnce({ deleted: 1, remaining: 0, failed: 0 });
    api.getLive.mockResolvedValueOnce(live()).mockResolvedValue(live({ old_posts: { live: 27, removable_now: 0, too_old_for_a_bot: 26, pinned: 1, by_kind: [] } }));
    render(<LiveNowPanel {...panelProps} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Remove old-format posts (4)' }));
    expect(api.deleteOld).not.toHaveBeenCalled();                              // asking first: nothing is removed yet
    expect(screen.getByText(/Remove 4 old-format posts from the public channel\? This cannot be undone\./)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Yes, remove them' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Removed 4 old-format posts from the channel.'));
    expect(api.deleteOld).toHaveBeenCalledTimes(2);
    expect(api.deleteOld).toHaveBeenCalledWith('prod', false, 'tok');
    expect(panelProps.onChanged).toHaveBeenCalled();
  });

  it('can be cancelled, is disabled without the token, and is not offered to a Collaborator', async () => {
    const { unmount } = render(<LiveNowPanel {...panelProps} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Remove old-format posts (4)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByRole('button', { name: 'Remove old-format posts (4)' })).toBeTruthy();
    expect(api.deleteOld).not.toHaveBeenCalled();
    unmount();
    const second = render(<LiveNowPanel {...panelProps} token={null} />);
    expect((await screen.findByRole('button', { name: 'Remove old-format posts (4)' })).hasAttribute('disabled')).toBe(true);
    second.unmount();
    render(<LiveNowPanel {...panelProps} isOwner={false} />);
    await screen.findByTestId('live-old-summary');
    expect(screen.queryByRole('button', { name: /Remove old-format posts/ })).toBeNull();
    expect(screen.getByText('Only the Owner account can remove posts.')).toBeTruthy();
  });

  it('says so when nothing old is left, and a wrong token is forgotten', async () => {
    api.getLive.mockResolvedValue(live({ old_posts: { live: 0, removable_now: 0, too_old_for_a_bot: 0, pinned: 0, by_kind: [] } }));
    const { unmount } = render(<LiveNowPanel {...panelProps} />);
    expect(await screen.findByText(/every post still in the channel is in the current format/)).toBeTruthy();
    unmount();
    api.getLive.mockResolvedValue(live());
    api.deleteOld.mockRejectedValue({ isAxiosError: true, response: { status: 401, data: { detail: { code: 'bad_token', message: 'The control token is missing or wrong.' } } } });
    render(<LiveNowPanel {...panelProps} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Remove old-format posts (4)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, remove them' }));
    await waitFor(() => expect(panelProps.onBadToken).toHaveBeenCalled());
  });
});

describe('The picture of a photo message', () => {
  beforeEach(() => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:picture'), revokeObjectURL: vi.fn() }));
  });

  it('loads the picture from the server and shows it', async () => {
    api.getImage.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
    render(<MessageImage id={7} alt="Picture of Daily market brief - card" />);
    expect(screen.getByTestId('message-image-placeholder').textContent).toContain('Loading the picture from Telegram');
    const img = (await screen.findByTestId('message-image')) as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('blob:picture');
    expect(img.getAttribute('alt')).toBe('Picture of Daily market brief - card');
    expect(api.getImage).toHaveBeenCalledWith(7);
  });

  it('says why when the picture cannot be shown instead of an empty box', async () => {
    api.getImage.mockRejectedValue({ isAxiosError: true, response: { status: 404, data: { detail: { code: 'not_found', message: 'Telegram no longer has this message (it may have been deleted in the channel).' } } } });
    render(<MessageImage id={7} alt="x" />);
    await waitFor(() => expect(screen.getByTestId('message-image-placeholder').textContent).toContain('Telegram no longer has this message'));
    expect(screen.queryByTestId('message-image')).toBeNull();
  });
});

const detail = (over: Partial<MessageDetail> = {}): MessageDetail =>
  ({
    id: 7, target: 'prod', message_id: 401, kind: 'report_card', kind_label: 'Daily market brief - card', format: 'current', has_image: true, content_type: 'photo',
    status: 'sent', preview: 'Stocks slip', text_length: 40, silent: true, pinned: false, edit_count: 0, sent_at: '2026-10-27T22:01:00+00:00', edited_at: null,
    deleted_at: null, deletable_until: '2026-10-29T22:01:00+00:00', can_edit: { ok: true, code: null, reason: null }, can_delete: { ok: true, code: null, reason: null },
    can_pin: { ok: true, code: null, reason: null }, text: '<b>Stocks slip</b>', original_text: '<b>Stocks slip</b>', disable_preview: true, buttons: [], limit: 1024, audit: [], ...over,
  }) as MessageDetail;

describe('Editing a post', () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute('open', '');
    };
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute('open');
    };
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:picture'), revokeObjectURL: vi.fn() }));
    api.getMessage.mockResolvedValue(detail());
    api.getImage.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
  });
  const modal = (props: Partial<React.ComponentProps<typeof MessageModal>> = {}) =>
    render(<MessageModal id={7} initialMode="edit" timezone="UTC" token={null} controlsEnabled isOwner onClose={vi.fn()} onChanged={vi.fn()} onBadToken={vi.fn()} {...props} />);

  it('tells a Collaborator the real reason (the Owner account is needed), not that the token is missing on the server', async () => {
    modal({ isOwner: false, controlsEnabled: true });
    const dialog = await screen.findByRole('dialog', { hidden: true });
    await waitFor(() => expect(within(dialog).getByText(/Only the Owner account can edit or delete posts/)).toBeTruthy());
    expect(dialog.textContent).not.toContain('TELEGRAM_CONTROL_TOKEN');
  });

  it('keeps the other two reasons for the Owner: the server switch, and the token not entered yet', async () => {
    const { unmount } = modal({ isOwner: true, controlsEnabled: false });
    expect(await screen.findByText('This action is switched off on the server (no TELEGRAM_CONTROL_TOKEN).')).toBeTruthy();
    unmount();
    modal({ isOwner: true, controlsEnabled: true, token: null });
    expect(await screen.findByText('Enter the control token at the top of the page to save changes.')).toBeTruthy();
  });

  it('shows the real picture of a photo message in the pop-up', async () => {
    modal({ initialMode: 'view' });
    expect(await screen.findByTestId('message-image')).toBeTruthy();
    expect(api.getImage).toHaveBeenCalledWith(7);
  });
});

describe('The format filter', () => {
  it('opens on the current post types and offers old and all, with the live count of each', () => {
    const onChange = vi.fn();
    const kinds = [
      { kind: 'market_report', count: 4, live: 4, last_sent_at: null, format: 'current' as const, label: 'Daily market brief' },
      { kind: 'digest_card', count: 25, live: 20, last_sent_at: null, format: 'old' as const, label: 'Daily digest - market card' },
    ];
    render(<MessageFilters value={DEFAULT_FILTERS} kinds={kinds} onChange={onChange} />);
    const current = screen.getByRole('button', { name: 'Current format (4)' });
    expect(current.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Old format (20)' }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_FILTERS, format: 'old' });
    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_FILTERS, format: '' });
  });
});
