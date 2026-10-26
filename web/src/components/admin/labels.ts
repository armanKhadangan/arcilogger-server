import type { TFunction } from 'i18next';

/** The label of a configuration path (`clock.ntp_server`), else the path itself. */
export function pathLabel(path: string, t: TFunction): string {
  return t(`admin.paths.${path}`, { defaultValue: path });
}

/** An effect of a change (`reload.collector`, `system.firewall`, `task.next_run:Seal`) in words. */
export function effectLabel(effect: string, t: TFunction): string {
  const [name, argument] = effect.split(':', 2);
  if (name === 'task.next_run') return t('admin.effects.taskNextRun', { task: argument ?? '' });
  return t(`admin.effects.${name ?? effect}`, { defaultValue: effect });
}
