import type { TFunction } from 'i18next';

/** The fields of a source in a change path (`sources.<name>.<field>`) → its column label. */
const SOURCE_FIELDS: Record<string, string> = {
  type: 'type',
  address: 'address',
  transport: 'transport',
  heartbeat_minutes: 'heartbeat',
  expected: 'expected',
  enabled: 'enabled',
};

/** The label of a configuration path (`clock.ntp_server`), else the path itself. */
export function pathLabel(path: string, t: TFunction): string {
  // A source's SNMP table and its two keys (Wave 5 §6 N1).
  const snmp = /^sources\.([^.]+)\.snmp\.([a-z_]+)$/.exec(path);
  if (snmp)
    return t('snmp.review.field', {
      name: snmp[1],
      field: t(`snmp.review.fields.${snmp[2] ?? ''}`, { defaultValue: snmp[2] }),
    });
  const key = /^snmp\.([^.]+)\.(auth_key|priv_key)$/.exec(path);
  if (key)
    return t(`snmp.review.${key[2] === 'auth_key' ? 'authKey' : 'privKey'}`, { name: key[1] });
  if (path.startsWith('sources.')) {
    const rest = path.slice('sources.'.length);
    const dot = rest.lastIndexOf('.');
    const column = dot > 0 ? SOURCE_FIELDS[rest.slice(dot + 1)] : undefined;
    if (column)
      return t('admin.review.sourceField', {
        name: rest.slice(0, dot),
        field: t(`settings.sources.columns.${column}`),
      });
    return t('admin.review.source', { name: rest });
  }
  return t(`admin.paths.${path}`, { defaultValue: path });
}

/** An effect of a change (`reload.collector`, `system.firewall`, `task.next_run:Seal`) in words. */
export function effectLabel(effect: string, t: TFunction): string {
  const [name, argument] = effect.split(':', 2);
  if (name === 'task.next_run') return t('admin.effects.taskNextRun', { task: argument ?? '' });
  return t(`admin.effects.${name ?? effect}`, { defaultValue: effect });
}
