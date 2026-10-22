import type { HTMLAttributes } from 'react';

import { cn } from '@/lib/utils';

/** Placeholder block shown while data loads. */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn('animate-pulse rounded-lg bg-subtle', className)}
      {...props}
    />
  );
}
