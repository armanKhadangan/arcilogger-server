import { useId } from 'react';

import { cn } from '@/lib/utils';

/** ARCILOGGER mark: an archive box with a check seal. Single colour (currentColor). */
export function LogoMark({ className, title }: { className?: string; title?: string }) {
  const maskId = useId();
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <defs>
        <mask id={maskId}>
          <rect width="32" height="32" fill="white" />
          <circle cx="23" cy="23" r="8.5" fill="black" />
        </mask>
      </defs>
      <g mask={`url(#${maskId})`}>
        <rect x="4" y="5" width="24" height="6" rx="1.5" />
        <path d="M6 11v14a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V11" />
        <path d="M13 16h6" />
      </g>
      <circle cx="23" cy="23" r="6.5" />
      <path d="m20.25 23.1 1.9 1.9 3.6-3.8" />
    </svg>
  );
}

/**
 * Mark + wordmark; `caption` (the organisation's name in the sidebar) goes under the wordmark,
 * cut off with an ellipsis when long. An empty caption shows nothing.
 */
export function Logo({
  className,
  collapsed = false,
  caption = '',
}: {
  className?: string;
  collapsed?: boolean;
  caption?: string;
}) {
  return (
    <span className={cn('flex min-w-0 items-center gap-2.5', className)}>
      <LogoMark
        className="size-7 shrink-0 text-sidebar-bar"
        title={collapsed ? 'ARCILOGGER' : undefined}
      />
      {collapsed ? null : (
        <span className="flex min-w-0 flex-col">
          <span className="text-base font-bold tracking-[0.04em] text-white">ARCILOGGER</span>
          {caption ? (
            <span
              data-testid="site-name"
              title={caption}
              className="truncate text-xs leading-4 text-sidebar-text"
            >
              {caption}
            </span>
          ) : null}
        </span>
      )}
    </span>
  );
}
