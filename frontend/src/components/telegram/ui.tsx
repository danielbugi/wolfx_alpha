// File: frontend/src/components/telegram/ui.tsx
// A native button with plain Tailwind. NextUI's Button variants (bordered / flat / isLoading) depend on the NextUI Tailwind plugin, which this app
// does not load, so they render without borders or fills here. Buttons in the Control Center use this instead.
import React from 'react';
import { clsx } from 'clsx';

type Variant = 'primary' | 'secondary' | 'danger' | 'warning' | 'ghost';
type Size = 'sm' | 'md' | 'icon';

const VARIANT: Record<Variant, string> = {
  primary: 'bg-slate-900 text-white hover:bg-slate-700',
  secondary: 'border border-black/[0.08] bg-white text-slate-700 shadow-xs hover:bg-slate-50',
  danger: 'bg-red-600 text-white hover:bg-red-700',
  warning: 'bg-amber-600 text-white hover:bg-amber-700',
  ghost: 'text-slate-600 hover:bg-black/[0.05]',
};
const SIZE: Record<Size, string> = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-3.5 py-1.5 text-sm',
  icon: 'p-1.5',
};

interface BtnProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export function Btn({ variant = 'secondary', size = 'md', className, type = 'button', ...rest }: BtnProps) {
  return (
    <button
      type={type}
      className={clsx(
        'press inline-flex items-center justify-center gap-1.5 rounded-lg font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40',
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...rest}
    />
  );
}
