// File: frontend/src/lib/navigation.ts
// The single list of dashboard routes, shared by Sidebar and MobileNav so they can never drift apart.
//
// Two groups: PRODUCT (the market-intelligence journey: overview -> screener -> stock -> deep value, plus markets and
// earnings) and INTERNAL (research, trading operations and ops tools). The split is a USER-EXPERIENCE separation only:
// hiding a link protects nothing, and every internal page is still reachable by URL and served by its API to any
// signed-in user. Access control lives on the backend (backend/auth/dependencies.py), not here.
import {
  BeakerIcon,
  BoltIcon,
  CalendarDaysIcon,
  ChartBarIcon,
  ClipboardDocumentListIcon,
  CpuChipIcon,
  FireIcon,
  FunnelIcon,
  GlobeAltIcon,
  HeartIcon,
  PaperAirplaneIcon,
  PresentationChartLineIcon,
  RectangleGroupIcon,
  ScaleIcon,
  Squares2X2Icon,
  UsersIcon,
} from '@heroicons/react/24/outline';
import type { ComponentType, SVGProps } from 'react';
import type { UserRole } from '@/contexts/AuthContext';

export interface NavItem {
  name: string;
  href: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  /** One line shown as a title/tooltip when the sidebar is collapsed to icons only. */
  description: string;
  /** Omitted = every signed-in role sees it. Present = only these roles do (e.g. Owner-only admin pages). */
  roles?: UserRole[];
}

export interface NavGroup {
  label: string;
  items: NavItem[];
  /** Internal tools: shown under a collapsible header that starts closed (it opens by itself on one of its pages). */
  internal?: boolean;
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Product',
    items: [
      { name: 'Overview', href: '/', icon: Squares2X2Icon, description: 'Market overview: breadth, sectors, movers' },
      { name: 'Sectors', href: '/sectors', icon: RectangleGroupIcon, description: 'Sector comparison and sector pages' },
      { name: 'Screener', href: '/screener', icon: FunnelIcon, description: 'Search & filter the universe' },
      { name: 'Deep Value', href: '/alerts', icon: ScaleIcon, description: 'Near-low stocks that pass balance-sheet checks (descriptive)' },
      { name: 'Markets', href: '/markets', icon: GlobeAltIcon, description: 'Futures, commodities, currencies, crypto' },
      { name: 'Earnings', href: '/earnings', icon: CalendarDaysIcon, description: 'Earnings calendar: who reports, day by day' },
    ],
  },
  {
    label: 'Internal',
    internal: true,
    items: [
      { name: 'Momentum Board', href: '/momentum', icon: FireIcon, description: 'Internal: momentum lists and leaders' },
      { name: 'Position Plans', href: '/strategy', icon: ChartBarIcon, description: 'Internal: ranked position plans' },
      { name: 'Strategies', href: '/strategies', icon: PresentationChartLineIcon, description: 'Internal: strategy outcomes, signals, data health' },
      { name: 'Track Record', href: '/track-record', icon: ClipboardDocumentListIcon, description: 'Internal: breakout signal track record' },
      { name: 'Quant Lab', href: '/lab', icon: BeakerIcon, description: 'Internal: historical research evidence' },
      { name: 'Telegram', href: '/telegram', icon: PaperAirplaneIcon, description: 'Internal: channel post control center' },
      { name: 'System Health', href: '/system-health', icon: HeartIcon, description: 'Internal: pipeline, data quality, ML' },
      { name: 'ML Stats', href: '/ml-stats', icon: CpuChipIcon, description: 'Internal: model accuracy & training data' },
      { name: 'Performance', href: '/performance', icon: BoltIcon, description: 'Internal: API & route latency' },
      { name: 'Users', href: '/users', icon: UsersIcon, description: 'Accounts & sessions (Owner only)', roles: ['owner'] },
    ],
  },
];

/** Product routes: the pages that carry the product disclaimer and must stay free of trade plans, sizing,
 * experimental model output and signal-performance claims (pinned by tests). `/stock/[symbol]` is a product page
 * reached from every table, so it is listed here although it has no menu entry. */
export const PRODUCT_ROUTE_PREFIXES: readonly string[] = [
  ...NAV_GROUPS.filter((g) => !g.internal).flatMap((g) => g.items.map((i) => i.href)),
  '/stock',
];

export function isProductRoute(pathname: string): boolean {
  return PRODUCT_ROUTE_PREFIXES.some((href) => isActiveRoute(pathname, href));
}

/** True when `href` is the current route or a page inside its section (`/markets/GC=F` keeps Markets highlighted).
 * `/stock/[symbol]` is deliberately unlisted, so a stock page highlights nothing. */
export function isActiveRoute(pathname: string, href: string): boolean {
  return pathname === href || (href !== '/' && pathname.startsWith(`${href}/`));
}

/** NAV_GROUPS filtered to what `role` may see, with any group left empty by the filter dropped entirely. */
export function navGroupsForRole(role: UserRole | undefined): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.roles || (role && item.roles.includes(role))),
  })).filter((group) => group.items.length > 0);
}
