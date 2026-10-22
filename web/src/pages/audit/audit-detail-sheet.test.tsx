/**
 * The detail drawer of "Denetim Kaydı": the hash of an entry is recomputed in the browser, except
 * for a target the API masked for a pseudonymous reader (`target_masked`) — that is not what was
 * hashed, so the drawer says why it cannot check it instead of reporting a changed entry.
 */

import { render, screen } from '@testing-library/react';
import { webcrypto } from 'node:crypto';

import vectors from '@/generated/audit-vectors.json';
import type { AuditEntry } from '@/lib/api-types';

import { AuditDetailSheet } from './audit-detail-sheet';

const ENTRIES = vectors.entries as { api: AuditEntry }[];

function entry(): AuditEntry {
  const found = ENTRIES[7]?.api;
  if (!found) throw new Error('vector missing');
  return found;
}

beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it('recomputes the hash of an entry shown as stored', async () => {
  render(<AuditDetailSheet entry={entry()} onClose={() => undefined} />);
  expect(await screen.findByText('Özet bu tarayıcıda yeniden hesaplandı: eşleşiyor')).toBeVisible();
  expect(screen.queryByText(/takma ad belirtecidir/)).toBeNull();
});

it('explains a masked target instead of reporting a mismatch', async () => {
  const masked = { ...entry(), target: 'usage:d:ad369bf88c2e8325', target_masked: true };
  render(<AuditDetailSheet entry={masked} onClose={() => undefined} />);
  expect(
    await screen.findByText(
      'Hedef takma adla gösteriliyor: özet bu tarayıcıda yeniden hesaplanamaz',
    ),
  ).toBeVisible();
  expect(screen.getByText(/takma ad belirtecidir/)).toBeVisible();
  expect(screen.getByText('usage:d:ad369bf88c2e8325')).toBeVisible();
  expect(screen.queryByText(/eşleşmiyor/)).toBeNull();
  expect(screen.queryByText('Kanonik kayıt (RFC 8785)')).toBeNull();
});

it('shows no hash blocks and says why when the API sent no hashes', async () => {
  const masked = {
    ...entry(),
    target: 'usage:d:ad369bf88c2e8325',
    target_masked: true,
    prev_hash: null,
    hash: null,
  };
  const { unmount } = render(<AuditDetailSheet entry={masked} onClose={() => undefined} />);
  expect(await screen.findByText(/özetler size gösterilmez/)).toBeVisible();
  expect(screen.queryByText('Önceki kaydın özeti')).toBeNull();
  expect(screen.queryByText('Bu kaydın özeti')).toBeNull();
  unmount();
  // Any other entry of such a reader: its content is shown, its hashes are not.
  const plain = { ...entry(), prev_hash: null, hash: null };
  render(<AuditDetailSheet entry={plain} onClose={() => undefined} />);
  expect(
    await screen.findByText('Takma ad görenlere özetler gösterilmez: zincir sunucuda doğrulanır'),
  ).toBeVisible();
  expect(screen.getByText('Kanonik kayıt (RFC 8785)')).toBeVisible();
  expect(screen.queryByText('Bu kaydın özeti')).toBeNull();
  expect(screen.queryByText(/eşleşmiyor/)).toBeNull();
});
