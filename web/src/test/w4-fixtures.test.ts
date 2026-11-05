import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { SystemHealth, TaskName } from '@/lib/system-health-types';

import { TASKS } from './w4-fixtures';

/*
 * The Sistem fixtures follow the server's task list (#153): a task added to `TaskName` or to the
 * API (W5-E added Renew and CrlFetch) fails here until `TASKS` has its row, in the server's order.
 */

/**
 * Every `TaskName` with its place in the server's order. `satisfies` makes a name missing here,
 * or one the type does not have, a type error.
 */
const ORDER = {
  Seal: 0,
  VerifyDaily: 1,
  VerifyWeekly: 2,
  VerifyMonthly: 3,
  Replica: 4,
  Retention: 5,
  Credit: 6,
  ClockCheck: 7,
  Backup: 8,
  Requests: 9,
  ApplySystem: 10,
  Renew: 11,
  CrlFetch: 12,
} satisfies Record<TaskName, number>;

const NAMES = (Object.keys(ORDER) as TaskName[]).sort((a, b) => ORDER[a] - ORDER[b]);

/** The captured answer of `GET /system/health` (`web/e2e/samples/system-health.json`). */
const CAPTURED = (
  JSON.parse(readFileSync(join(process.cwd(), 'e2e', 'samples', 'system-health.json'), 'utf8')) as {
    health: SystemHealth;
  }
).health;

describe('the Sistem fixtures', () => {
  it('have one task row per TaskName, in the server order', () => {
    expect(TASKS.map((row) => row.task)).toEqual(NAMES);
  });

  it('list the tasks the API answers, in its order and with its schedule texts', () => {
    const schedule = (rows: readonly SystemHealth['tasks'][number][]) =>
      rows.map(({ task, schedule_tr, schedule_en }) => ({ task, schedule_tr, schedule_en }));
    expect(CAPTURED.tasks.map((row) => row.task)).toEqual(NAMES);
    expect(schedule(TASKS)).toEqual(schedule(CAPTURED.tasks));
  });

  it('show Renew and CrlFetch as the captured sample does', () => {
    for (const name of ['Renew', 'CrlFetch'] as const) {
      const fixture = TASKS.find((row) => row.task === name);
      const captured = CAPTURED.tasks.find((row) => row.task === name);
      expect(fixture, name).toEqual(captured);
    }
  });
});
