import { useCallback } from 'react';

import { agentWritable } from '@/lib/admin-requests';
import { useAdminConfig, useAdminStatus, useApplySection, useCheckSection } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import type { AdminSection, SectionBodies } from '@/lib/settings-types';

import { useSectionFlow } from './use-section-flow';

type ConfigSection = Exclude<AdminSection, 'sources'>;

const NO_FIELDS: readonly string[] = [];

/**
 * Everything a page of the machine configuration needs for one section: the configuration and
 * the administration service's status, the permission, the section's `etag` and the save flow
 * (`POST …/check`, then `PUT /admin/config/{section}`). `fields`: the configuration paths the
 * page shows at a field, a constant of the page (a refusal of any other one is shown above the
 * form).
 */
export function useMachineSection<S extends ConfigSection>(
  section: S,
  {
    permission = 'settings.manage',
    fields = NO_FIELDS,
  }: { permission?: string; fields?: readonly string[] } = {},
) {
  const { can } = useAuth();
  const config = useAdminConfig();
  const status = useAdminStatus();
  const check = useCheckSection();
  const apply = useApplySection();
  const { mutateAsync: checkAsync, reset: resetCheck } = check;
  const { mutateAsync: applyAsync, reset: resetApply } = apply;
  // A body may hold a new credential: the mutations forget it as soon as they are answered.
  const flow = useSectionFlow<SectionBodies[S]>({
    check: useCallback(
      async (body: SectionBodies[S]) => {
        try {
          return await checkAsync({ section, body });
        } finally {
          resetCheck();
        }
      },
      [checkAsync, resetCheck, section],
    ),
    submit: useCallback(
      async (body: SectionBodies[S]) => {
        try {
          return await applyAsync({ section, body });
        } finally {
          resetApply();
        }
      },
      [applyAsync, resetApply, section],
    ),
    fields: useCallback(() => fields, [fields]),
  });
  const agent = status.data?.agent.state ?? null;
  return {
    config,
    status,
    flow,
    agent,
    canEdit: can(permission),
    writable: can(permission) && agentWritable(agent),
    etag: config.data?.etags[section] ?? '',
    restartRequired: status.data?.restart_required ?? false,
  };
}
