import { formatDateTime } from './format';

export interface RecoveryFileLabels {
  heading: string;
  account: string;
  created: string;
}

/**
 * Text of the downloaded recovery-code file. CRLF line ends: it is opened with Notepad on the
 * Windows PCs; the time is the site time (Europe/Istanbul) like everywhere in the UI.
 */
export function recoveryCodesFile(
  codes: readonly string[],
  account: string,
  labels: RecoveryFileLabels,
  now: Date = new Date(),
): string {
  const lines = [
    labels.heading,
    `${labels.account}: ${account}`,
    `${labels.created}: ${formatDateTime(now)}`,
    '',
    ...codes.map((code, index) => `${String(index + 1).padStart(2, ' ')}. ${code}`),
    '',
  ];
  return lines.join('\r\n');
}

/** File name of the download (the user name is ASCII by the server's rule). */
export function recoveryCodesFileName(account: string): string {
  return `arcilogger-kurtarma-kodlari-${account.replace(/[^a-z0-9._-]/gi, '_')}.txt`;
}
