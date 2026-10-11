// File: frontend/src/lib/navigation.ts
// The single list of dashboard routes, shared by Sidebar and MobileNav so they can never drift apart.
import {
  BellAlertIcon,
  BoltIcon,
  ChartBarIcon,
  CpuChipIcon,
  FunnelIcon,
  GlobeAltIcon,
  HeartIcon,
  PaperAirplaneIcon,
  PresentationChartLineIcon,
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
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Markets',
    items: [
      { name: 'Dashboard', href: '/', icon: Squares2X2Icon, description: 'Overview & breakouts' },
      { name: 'Markets', href: '/markets', icon: GlobeAltIcon, description: 'Futures, commodities, currencies, crypto' },
      { name: 'Screener', href: '/screener', icon: FunnelIcon, description: 'Search & filter the universe' },
      { name: 'Position Plans', href: '/strategy', icon: ChartBarIcon, description: 'Ranked position plans' },
      { name: 'Strategies', href: '/strategies', icon: PresentationChartLineIcon, description: 'Strategy intelligence: outcomes, signals, data health' },
      { name: 'Alerts', href: '/alerts', icon: BellAlertIcon, description: 'Deep-value / turnaround watch' },
      { name: 'Telegram', href: '/telegram', icon: PaperAirplaneIcon, description: 'Channel post control center' },
    ],
  },
  {
    label: 'Ops',
    items: [
      { name: 'System Health', href: '/system-health', icon: HeartIcon, description: 'Pipeline, data quality, ML' },
      { name: 'ML Stats', href: '/ml-stats', icon: CpuChipIcon, description: 'Model accuracy & training data' },
      { name: 'Performance', href: '/performance', icon: BoltIcon, description: 'API & route latency' },
      { name: 'Users', href: '/users', icon: UsersIcon, description: 'Accounts & sessions (Owner only)', roles: ['owner'] },
    ],
  },
];

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
