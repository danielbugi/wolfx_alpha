// File: frontend/src/lib/navigation.ts
// The single list of dashboard routes, shared by Sidebar and MobileNav so they can never drift apart.
import {
  BellAlertIcon,
  BoltIcon,
  ChartBarIcon,
  CpuChipIcon,
  FunnelIcon,
  HeartIcon,
  PaperAirplaneIcon,
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
      { name: 'Screener', href: '/screener', icon: FunnelIcon, description: 'Search & filter the universe' },
      { name: 'Strategy', href: '/strategy', icon: ChartBarIcon, description: 'Ranked position plans' },
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

/** True when `href` is the current route. Exact match only — none of these routes has a nested child in the nav (e.g. `/stock/[symbol]` is deliberately unlisted). */
export function isActiveRoute(pathname: string, href: string): boolean {
  return pathname === href;
}

/** NAV_GROUPS filtered to what `role` may see, with any group left empty by the filter dropped entirely. */
export function navGroupsForRole(role: UserRole | undefined): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.roles || (role && item.roles.includes(role))),
  })).filter((group) => group.items.length > 0);
}
