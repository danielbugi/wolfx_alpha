// InfoTip: opens on click/tap without triggering the parent's click (a sortable header), stays attached while the page
// scrolls, and closes on Escape, a click elsewhere, a second click, or when its button leaves the screen.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import InfoTip from '@/components/ui/InfoTip';

const rect = (top: number) => ({ top, bottom: top + 16, left: 100, right: 116, width: 16, height: 16, x: 100, y: top, toJSON: () => ({}) }) as DOMRect;

describe('InfoTip', () => {
  it('opens with the label and text, without clicking through to its parent', () => {
    const parent = vi.fn();
    render(
      <div onClick={parent}>
        <InfoTip label="Volume ratio" text="Session volume vs its 10-day average volume." />
      </div>,
    );
    const b = screen.getByRole('button', { name: 'About Volume ratio' });
    b.getBoundingClientRect = () => rect(40);
    fireEvent.click(b);
    expect(screen.getByRole('tooltip').textContent).toBe('Volume ratioSession volume vs its 10-day average volume.');
    expect(b.getAttribute('aria-expanded')).toBe('true');
    expect(parent).not.toHaveBeenCalled();
    fireEvent.click(b);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('follows its button on scroll, and closes on Escape, a click elsewhere, or when the button scrolls away', () => {
    render(<InfoTip label="ATR %" text="Average true range as % of the close." />);
    const b = screen.getByRole('button', { name: 'About ATR %' });
    let top = 40;
    b.getBoundingClientRect = () => rect(top);
    fireEvent.click(b);
    top = 80;
    act(() => {
      window.dispatchEvent(new Event('scroll'));
    });
    expect((screen.getByRole('tooltip') as HTMLElement).style.top).toBe('102px'); // 80 + 16 + 6
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.click(b);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.click(b);
    top = -200;
    act(() => {
      window.dispatchEvent(new Event('scroll'));
    });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });
});
