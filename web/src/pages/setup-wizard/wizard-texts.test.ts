import i18n from '@/lib/i18n';
import { actionKey, KNOWN_ACTIONS } from '@/pages/audit/audit-labels';

/*
 * The fixed texts of the first-run wizard, its entry on /setup and the two Wave 4 audit actions
 * (Wave 4 contract 2.8.5), in both languages.
 */

const tr = i18n.getFixedT('tr');
const en = i18n.getFixedT('en');

const FIXED: [key: string, tr: string, en: string][] = [
  ['setupWizard.title', 'İlk kurulum sihirbazı', 'First-run wizard'],
  ['setupWizard.skip', 'Bu adımı şimdilik atla', 'Skip this step for now'],
  ['setupWizard.open', "Ayarlar'da aç", 'Open in Settings'],
  ['setupWizard.back', 'Kurulum sihirbazına dön', 'Back to the first-run wizard'],
  ['setup.stepWizard', 'Kurulum sihirbazı', 'First-run wizard'],
  ['setup.stepWizardHint', "Girişten sonra, Ayarlar'da", 'After sign-in, in Settings'],
  [
    'auditActions.compliance_report_export',
    'Uyum Durumu Raporu indirildi',
    'Compliance status report exported',
  ],
  [
    'auditActions.compliance_config_backup_confirm',
    'Yapılandırma yedeği onaylandı',
    'Configuration backup confirmed',
  ],
];

describe('the fixed texts of the first-run wizard', () => {
  it.each(FIXED)('%s', (key, trText, enText) => {
    expect(tr(key)).toBe(trText);
    expect(en(key)).toBe(enText);
  });

  it('labels the two audit actions', () => {
    for (const action of ['compliance.report_export', 'compliance.config_backup_confirm']) {
      expect(KNOWN_ACTIONS).toContain(action);
      expect(i18n.exists(actionKey(action), { lng: 'tr' })).toBe(true);
    }
  });
});
