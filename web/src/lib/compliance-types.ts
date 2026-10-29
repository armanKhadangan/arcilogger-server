/** Mirror of api/src/arcilogger_api/compliance/models.py (Wave 4 contract 2.3.1). */
import type { AlertSeverity, DateTimeString } from './api-types';

export type ComplianceTone = 'ok' | 'warning' | 'error' | 'neutral';
export type ComplianceCardId =
  | 'sources'
  | 'seal_yesterday'
  | 'chain'
  | 'retention'
  | 'tsa'
  | 'clock'
  | 'content_filter'
  | 'lease_evidence'
  | 'replica'
  | 'backup';
/** Nav key of the page with the details (web/src/nav.ts). */
export type ComplianceLinkPage = 'sources' | 'sealing' | 'search' | 'settings' | 'system';

export interface ComplianceFact {
  key: string;
  label_tr: string;
  label_en: string;
  value_tr: string;
  value_en: string;
}

export interface ComplianceAlertRef {
  id: number;
  rule: string;
  severity: AlertSeverity;
  opened_at: DateTimeString;
}

export interface ComplianceCard {
  id: ComplianceCardId;
  number: number;
  tone: ComplianceTone;
  title_tr: string;
  title_en: string;
  reason_tr: string;
  reason_en: string;
  facts: ComplianceFact[];
  alerts: ComplianceAlertRef[];
  page: ComplianceLinkPage;
  doc_tr: string;
  doc_en: string;
  verify_on_site_tr: string | null;
  verify_on_site_en: string | null;
  checked_at: DateTimeString;
}

export interface ComplianceCounts {
  ok: number;
  warning: number;
  error: number;
  neutral: number;
}

export interface ComplianceStatus {
  generated_at: DateTimeString;
  site_name: string | null;
  counts: ComplianceCounts;
  cards: ComplianceCard[];
  notice_tr: string;
  notice_en: string;
}

export interface ConfigBackupOut {
  confirmed_at: DateTimeString;
  config_sha256: string;
}
