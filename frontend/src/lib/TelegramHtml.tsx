// File: frontend/src/lib/TelegramHtml.tsx
'use client';

import React, { useMemo } from 'react';
import { clsx } from 'clsx';

/**
 * Renders the HTML subset Telegram accepts (b, i, u, s, code, pre, a, blockquote, tg-spoiler) as React elements — never through
 * dangerouslySetInnerHTML. Every other tag is dropped (its text is kept), and a link is only clickable for http(s) / tg / mailto, so a
 * pasted `javascript:` href cannot run. This is a PREVIEW of what the channel shows; Telegram itself is the authority on validity.
 */
const SAFE_HREF = /^(https?:\/\/|tg:\/\/|mailto:)/i;

function convert(node: ChildNode, key: number): React.ReactNode {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  const el = node as HTMLElement;
  const children = Array.from(el.childNodes).map((child, i) => convert(child, i));
  switch (el.tagName.toLowerCase()) {
    case 'b':
    case 'strong':
      return <strong key={key}>{children}</strong>;
    case 'i':
    case 'em':
      return <em key={key}>{children}</em>;
    case 'u':
    case 'ins':
      return <u key={key}>{children}</u>;
    case 's':
    case 'strike':
    case 'del':
      return <s key={key}>{children}</s>;
    case 'code':
      return <code key={key} className="rounded bg-slate-200/70 px-1 font-mono text-[0.9em]">{children}</code>;
    case 'pre':
      return <pre key={key} className="my-1 overflow-x-auto rounded bg-slate-200/70 p-2 font-mono text-xs">{children}</pre>;
    case 'blockquote':
      return <blockquote key={key} className="my-1 border-l-2 border-sky-400 pl-2 text-slate-700">{children}</blockquote>;
    case 'a': {
      const href = el.getAttribute('href') ?? '';
      return SAFE_HREF.test(href) ? (
        <a key={key} href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-sky-700 underline">{children}</a>
      ) : (
        <span key={key}>{children}</span>
      );
    }
    case 'tg-spoiler':
      return <span key={key} title="Spoiler" className="rounded bg-slate-300 text-transparent hover:text-inherit">{children}</span>;
    default:
      return <React.Fragment key={key}>{children}</React.Fragment>;
  }
}

export default function TelegramHtml({ html, className }: { html: string; className?: string }) {
  const content = useMemo<React.ReactNode>(() => {
    if (typeof DOMParser === 'undefined') return html.replace(/<[^>]+>/g, '');
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return Array.from(doc.body.childNodes).map((child, i) => convert(child, i));
  }, [html]);
  return <div className={clsx('whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-800', className)}>{content}</div>;
}
