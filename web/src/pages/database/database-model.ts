import type { TFunction } from 'i18next';

import { formatBytes, formatNumber } from '@/lib/format';
import type { Lang } from '@/lib/format';
import type { DatabaseDetails, DatabaseGrowth } from '@/lib/system-types';

/** "PostgreSQL 16.4" or "PostgreSQL 16.4 ve TimescaleDB 2.30.2" (the version once read). */
export function engineText(database: DatabaseDetails, t: TFunction): string {
  const version = database.server_version ?? '';
  if (database.storage === 'timescaledb' || database.timescaledb_version !== null) {
    return t('database.engine.timescaledb', {
      version,
      extension: database.timescaledb_version ?? '',
    }).trim();
  }
  return t('database.engine.postgresql', { version }).trim();
}

/** The daily growth as a sentence: the bytes a day, too few samples, or not growing. */
export function growthText(growth: DatabaseGrowth, t: TFunction, lang: Lang): string {
  switch (growth.state) {
    case 'estimated':
      return t('database.size.perDay', { size: formatBytes(growth.bytes_per_day ?? 0, lang) });
    case 'insufficient':
      return t('database.size.insufficient', { samples: formatNumber(growth.samples, 0, lang) });
    case 'not_growing':
      return t('database.size.notGrowing');
  }
}
