/**
 * Display helpers for the TOTP enrolment.
 *
 * The QR code arrives as an SVG document from our own API. It is shown through `<img src="data:…">`
 * rather than inserted as markup: an image cannot run scripts or load anything, whatever the
 * SVG contains, and the CSP allows `img-src data:` (deploy/windows/caddy/Caddyfile).
 */

/** `data:` URI of an SVG document (percent-encoded, no base64 needed). */
export function svgDataUri(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** `JBSW Y3DP EHPK 3PXP` — the base32 key in groups of four for typing it by hand. */
export function groupSecret(secret: string): string {
  return (secret.replace(/\s+/g, '').match(/.{1,4}/g) ?? []).join(' ');
}
