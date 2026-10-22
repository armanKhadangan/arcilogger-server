/**
 * Saving files the API sent, and checking them against the hash the API announced.
 *
 * The official report carries its SHA-256 in `X-Arci-Sha256`, an evidence package in
 * `Repr-Digest` (RFC 9530, `sha-256=:<base64>:`). The browser recomputes the hash of the bytes
 * it actually received (Web Crypto; only in a secure context — HTTPS or localhost) so the user
 * sees "the file on your disk is the one the server recorded". Very large packages are not
 * hashed in the browser (the whole file would have to be in memory); the recipient checks the
 * package with its own verifier anyway (DENETCI-KILAVUZU).
 */

/** Largest file hashed in the browser (bytes). */
export const BROWSER_HASH_LIMIT = 256 * 1024 * 1024;

/** Offer a Blob as a file download. */
export function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
  // Revoked later: some browsers start reading the object URL after `click()` returns.
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 60_000);
}

function hex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

function subtleCrypto(): SubtleCrypto | null {
  const subtle = (globalThis.crypto as Partial<Crypto> | undefined)?.subtle;
  return typeof subtle?.digest === 'function' ? subtle : null;
}

/** Bytes of a Blob (`Blob.arrayBuffer` is missing in some test environments). */
function bytesOf(blob: Blob): Promise<ArrayBuffer> {
  if (typeof blob.arrayBuffer === 'function') return blob.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve(reader.result as ArrayBuffer);
    };
    reader.onerror = () => {
      reject(reader.error ?? new Error('cannot read the file'));
    };
    reader.readAsArrayBuffer(blob);
  });
}

/** SHA-256 (hex) of the Blob, `null` when Web Crypto is unavailable or the file is too large. */
export async function sha256Hex(blob: Blob): Promise<string | null> {
  const subtle = subtleCrypto();
  if (!subtle || blob.size > BROWSER_HASH_LIMIT) return null;
  return hex(await subtle.digest('SHA-256', await bytesOf(blob)));
}

/** The SHA-256 of a `Repr-Digest` header as hex (`sha-256=:base64:`), else `null`. */
export function reprDigestSha256(header: string | null): string | null {
  if (!header) return null;
  const match = /(?:^|,)\s*sha-256=:([A-Za-z0-9+/=]+):/i.exec(header);
  if (!match?.[1]) return null;
  try {
    const binary = atob(match[1]);
    if (binary.length !== 32) return null;
    let text = '';
    for (let index = 0; index < binary.length; index += 1)
      text += binary.charCodeAt(index).toString(16).padStart(2, '0');
    return text;
  } catch {
    return null;
  }
}

/** Outcome of the browser's check of a downloaded file. */
export type HashCheck =
  | { state: 'match'; sha256: string }
  | { state: 'mismatch'; expected: string; actual: string }
  | { state: 'unchecked'; expected: string | null; reason: 'no-crypto' | 'too-large' | 'no-hash' };

/** Compare the received bytes with the hash the server announced. */
export async function checkDownload(blob: Blob, expected: string | null): Promise<HashCheck> {
  if (!expected) return { state: 'unchecked', expected: null, reason: 'no-hash' };
  if (blob.size > BROWSER_HASH_LIMIT) return { state: 'unchecked', expected, reason: 'too-large' };
  const actual = await sha256Hex(blob);
  if (actual === null) return { state: 'unchecked', expected, reason: 'no-crypto' };
  return actual === expected.toLowerCase()
    ? { state: 'match', sha256: actual }
    : { state: 'mismatch', expected, actual };
}
