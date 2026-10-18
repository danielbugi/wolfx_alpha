// Product / Internal navigation: every page is in exactly one place, internal tools are never in the product group,
// and the product-route predicate the disclaimer and the content guards rely on matches the menu.
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { NAV_GROUPS, PRODUCT_ROUTE_PREFIXES, isProductRoute, navGroupsForRole } from '@/lib/navigation';

const APP = path.resolve(__dirname, '../../app');

function pageRoutes(dir = APP, base = ''): string[] {
  const out: string[] = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory() && e.name !== '__tests__') out.push(...pageRoutes(path.join(dir, e.name), `${base}/${e.name}`));
    else if (e.name === 'page.tsx') out.push(base || '/');
  }
  return out;
}

const product = NAV_GROUPS.find((g) => g.label === 'Product')!;
const internal = NAV_GROUPS.find((g) => g.label === 'Internal')!;

describe('navigation groups', () => {
  it('has exactly a Product group and a collapsible Internal group', () => {
    expect(NAV_GROUPS.map((g) => g.label)).toEqual(['Product', 'Internal']);
    expect(product.internal).toBeFalsy();
    expect(internal.internal).toBe(true);
  });

  it('puts the market-intelligence journey in Product, with Deep Value on the existing /alerts URL', () => {
    expect(product.items.map((i) => [i.name, i.href])).toEqual([
      ['Overview', '/'],
      ['Screener', '/screener'],
      ['Deep Value', '/alerts'],
      ['Markets', '/markets'],
      ['Earnings', '/earnings'],
    ]);
    expect(product.items.some((i) => /alert/i.test(i.name))).toBe(false);
  });

  it('keeps every research, trading-operations and ops page in Internal, none in Product', () => {
    const internalHrefs = internal.items.map((i) => i.href);
    for (const href of ['/momentum', '/strategy', '/strategies', '/track-record', '/lab', '/telegram', '/system-health', '/ml-stats', '/performance', '/users']) {
      expect(internalHrefs).toContain(href);
      expect(isProductRoute(href)).toBe(false);
      expect(isProductRoute(`${href}/x`)).toBe(false);
    }
  });

  it('lists every page of the app in exactly one group (dynamic stock/market pages and login excepted)', () => {
    const listed = NAV_GROUPS.flatMap((g) => g.items.map((i) => i.href));
    expect(new Set(listed).size).toBe(listed.length);
    const unlisted = pageRoutes().filter((r) => !listed.includes(r));
    // /stock/[symbol] and /markets/[symbol] are reached from tables; /strategies/[strategyKey] from Strategies; /login has no chrome.
    expect(unlisted.sort()).toEqual(['/login', '/markets/[symbol]', '/stock/[symbol]', '/strategies/[strategyKey]']);
  });

  it('treats stock pages as product pages and the Users page as owner-only', () => {
    expect(PRODUCT_ROUTE_PREFIXES).toContain('/stock');
    expect(isProductRoute('/stock/AAPL')).toBe(true);
    expect(isProductRoute('/markets/GC=F')).toBe(true);
    expect(isProductRoute('/')).toBe(true);
    expect(isProductRoute('/login')).toBe(false);
    const forCollaborator = navGroupsForRole('collaborator').flatMap((g) => g.items.map((i) => i.href));
    expect(forCollaborator).not.toContain('/users');
    expect(navGroupsForRole('owner').flatMap((g) => g.items.map((i) => i.href))).toContain('/users');
  });
});
