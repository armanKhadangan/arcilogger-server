import { useCallback } from 'react';

import { agentWritable } from '@/lib/admin-requests';
import {
  useAdminConfig,
  useAdminStatus,
  useApplySection,
  useCheckSection,
  type ConfigChange,
  type SectionChange,
} from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import type { AdminSection, SectionBodies } from '@/lib/settings-types';

import { useSectionFlow } from './use-section-flow';

type ConfigSection = Exclude<AdminSection, 'sources'>;

/**
 * Everything a page of the machine configuration needs for one section: the configuration and
 * the administration service's status, the permission, the section's `etag` and the save flow
 * (`POST …/check`, then `PUT /admin/config/{section}`).
 */
export function useMachineSection<S extends ConfigSection>(
  section: S,
  permission = 'settings.manage',
) {
  const { can } = useAuth();
  const config = useAdminConfig();
  const status = useAdminStatus();
  const check = useCheckSection();
  const apply = useApplySection();
  const { mutateAsync: checkAsync } = check;
  const { mutateAsync: applyAsync } = apply;
  const flow = useSectionFlow<SectionBodies[S]>({
    check: useCallback(
      (body: SectionBodies[S]) => checkAsync({ section, body } as SectionChange),
      [checkAsync, section],
    ),
    submit: useCallback(
      (body: SectionBodies[S]) => applyAsync({ section, body } as ConfigChange),
      [applyAsync, section],
    ),
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
