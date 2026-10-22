import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { useLicense } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { currentLang } from '@/lib/i18n';
import {
  bannersFor,
  bannerText,
  LICENSE_ADMIN_PERMISSION,
  LICENSE_PAGE,
  MAX_BANNERS,
} from '@/lib/license';

/**
 * The licence banners above every page (licensing contract 8.3): the state's banner first, then
 * (administrators only) limits and maintenance; at most two. Danger banners are announced at once
 * (`role="alert"`), the others politely (`role="status"`). Administrators get a link to the
 * licence page, everyone else the sentence to contact them. Nothing while the licence is loading
 * or could not be read.
 */
export function LicenseBanner() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const license = useLicense();
  if (!license.data) return null;
  const isAdmin = can(LICENSE_ADMIN_PERMISSION);
  const banners = bannersFor(license.data, { isAdmin }).slice(0, MAX_BANNERS);
  if (banners.length === 0) return null;
  const lang = currentLang();
  return (
    <div className="mb-4 flex flex-col gap-2" data-testid="license-banners">
      {banners.map((item) => (
        <Alert key={item.id} tone={item.tone} data-testid={`license-banner-${item.id}`}>
          {bannerText(item, t, lang)}{' '}
          {isAdmin ? (
            <Link
              to={LICENSE_PAGE}
              className="font-medium whitespace-nowrap underline underline-offset-2"
            >
              {t('license.banner.pageLink')}
            </Link>
          ) : (
            t('license.banner.contactAdmin')
          )}
        </Alert>
      ))}
    </div>
  );
}
