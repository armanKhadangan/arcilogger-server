import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import { formatBytes } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { DriveInfo } from '@/lib/system-types';
import { cn } from '@/lib/utils';

import { DRIVE_STYLE, driveLevel, formatPercent, type DriveLevel } from './system-model';

const FILL: Record<DriveLevel, string> = {
  ok: 'bg-success',
  warning: 'bg-warning',
  critical: 'bg-danger',
};

function clamp(value: number): number {
  return Math.min(100, Math.max(0, value));
}

/**
 * One drive: its letter and role, a meter of the used share with the warning and critical marks
 * of the server (`warning_percent`, `critical_percent`), and the same as text — the percentage,
 * a badge of icon and text for the level, and the bytes — so nothing depends on the colour.
 * `compact` (the Panel's card) leaves out the line of bytes and marks under the meter; its
 * screen-reader value still has the bytes.
 */
export function DriveMeter({
  drive,
  compact = false,
  className,
}: {
  drive: DriveInfo;
  compact?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const level = driveLevel(drive);
  const style = DRIVE_STYLE[level];
  const full = t('system.drive.full', { percent: formatPercent(drive.used_percent, lang) });
  const usage = t('system.drive.usage', {
    used: formatBytes(drive.used_bytes, lang),
    total: formatBytes(drive.total_bytes, lang),
    free: formatBytes(drive.free_bytes, lang),
  });
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)} data-testid="drive-meter">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="font-mono text-sm font-semibold text-foreground">{drive.path}</span>
          <span className="text-13 text-muted">{t(`system.drive.role.${drive.role}`)}</span>
        </span>
        <span className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground tabular">{full}</span>
          <Badge tone={style.tone} icon={style.icon}>
            {t(style.label)}
          </Badge>
        </span>
      </div>
      <div
        role="meter"
        aria-label={t('system.drive.meter', { path: drive.path })}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={drive.used_percent}
        aria-valuetext={`${full} (${usage})`}
        className="relative h-2.5 w-full rounded-full bg-subtle ring-1 ring-border ring-inset"
      >
        <div
          className={cn('h-full rounded-full', FILL[level])}
          style={{ width: `${clamp(drive.used_percent)}%` }}
        />
        {[drive.warning_percent, drive.critical_percent].map((mark) => (
          <span
            key={mark}
            aria-hidden="true"
            className="absolute -top-1 -bottom-1 w-0.5 -translate-x-1/2 rounded-full bg-foreground"
            style={{ left: `${clamp(mark)}%` }}
          />
        ))}
      </div>
      {compact ? null : (
        <div className="flex flex-wrap justify-between gap-x-3 gap-y-0.5 text-xs text-muted">
          <span className="tabular">{usage}</span>
          <span className="tabular">
            {t('system.drive.marks', {
              warning: formatPercent(drive.warning_percent, lang),
              critical: formatPercent(drive.critical_percent, lang),
            })}
          </span>
        </div>
      )}
    </div>
  );
}
