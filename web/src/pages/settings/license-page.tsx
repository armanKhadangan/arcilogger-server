import { ArrowLeft, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { PageSkeleton } from '@/components/layout/route-frames';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useLicense } from '@/lib/api-hooks';

import {
  AboutFields,
  ActivationFields,
  HistoryTable,
  LicenseFileForm,
  LicenseSection,
  LimitsTable,
  ModuleTable,
  StatusFields,
} from './license-parts';

/**
 * Ayarlar → Lisans (`settings.manage`; licensing contract 8.2): the state with the module table
 * and the limits, activation (request code to e-mail to the licensor), the licence file (upload,
 * removal; both after a re-authentication, refusals with the API's texts), the licence history
 * and "Hakkında" (version, release date, rights holder, time source). Everything comes from
 * `GET /license`; the server decides every state.
 */
export function LicensePage() {
  const { t } = useTranslation();
  const license = useLicense();
  const status = license.data;
  return (
    <div>
      <Button asChild variant="link" size="sm" className="mb-2">
        <Link to="/settings">
          <ArrowLeft aria-hidden="true" />
          {t('nav.settings')}
        </Link>
      </Button>
      <PageHeader title={t('license.page.title')} description={t('license.page.description')} />
      {status ? (
        <div className="flex flex-col gap-4">
          <LicenseSection title={t('license.page.status.title')} testId="license-section-status">
            <StatusFields status={status} />
            <h3 className="text-13 font-semibold text-foreground">{t('license.module.title')}</h3>
            <ModuleTable modules={status.modules} />
            <h3 className="text-13 font-semibold text-foreground">
              {t('license.page.limits.title')}
            </h3>
            <LimitsTable status={status} />
          </LicenseSection>
          <LicenseSection
            title={t('license.page.activation.title')}
            description={t('license.page.activation.description')}
            testId="license-section-activation"
          >
            <ActivationFields status={status} />
          </LicenseSection>
          <LicenseSection
            title={t('license.page.file.title')}
            description={t('license.page.file.description')}
            testId="license-section-file"
          >
            <LicenseFileForm status={status} />
          </LicenseSection>
          <LicenseSection title={t('license.page.history.title')} testId="license-section-history">
            <HistoryTable status={status} />
          </LicenseSection>
          <LicenseSection title={t('license.page.about.title')} testId="license-section-about">
            <AboutFields status={status} />
          </LicenseSection>
        </div>
      ) : license.isError ? (
        <Alert tone="danger" title={t('license.page.loadFailed')}>
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(license.error)}
            <Button size="sm" variant="secondary" onClick={() => void license.refetch()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : (
        <PageSkeleton />
      )}
    </div>
  );
}
