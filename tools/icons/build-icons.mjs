// ARCILOGGER's Windows icons, drawn from the web interface's own symbol (web/public/favicon.svg).
//
//   cd web && npm ci && cd .. && node tools/icons/build-icons.mjs
//
// writes deploy/windows/desktop/icons/:
//   arcilogger.ico          the program: Setup, the uninstall entry, the "ARCILOGGER" shortcuts
//   tray-<level>.ico        the status window's notification-area icon for each level of
//                           core/src/arcilogger_core/desktop/status.py (ok, warn, unknown, critical)
//
// The symbol is drawn in white on a rounded square: teal for the program, the level's colour (the
// same COLOURS as the status window) for the tray. Chromium (the Playwright of web/) rasterises
// the SVG at every size, so small sizes get their own pixels, not a scaled-down 256 px image.
// Each .ico holds 32-bit BMP images (alpha) up to 64 px and a PNG at 256 px: the layout Windows
// Explorer, the notification area and Inno Setup all read. tests/test_windows_icons.py checks the
// files; run this script again after changing the symbol or a colour, and commit the result.

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const { chromium } = createRequire(join(root, 'web', 'package.json'))('playwright');

const TEAL = '#0F766E';
// status.py COLOURS (the window's dots and headline)
const LEVELS = { ok: '#1a7f37', warn: '#b35900', unknown: '#6e6e6e', critical: '#c62828' };
const PROGRAM_SIZES = [16, 20, 24, 32, 40, 48, 64, 256];
const TRAY_SIZES = [16, 20, 24, 32, 40, 48, 64];

/** The favicon's drawing (everything inside <svg>), with its stroke colour and width replaced. */
function glyph() {
  const svg = readFileSync(join(root, 'web', 'public', 'favicon.svg'), 'utf8');
  const inner = svg.replace(/^[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '');
  if (!inner.includes('<mask') || !inner.includes('<circle')) {
    throw new Error('web/public/favicon.svg changed shape: check the icon drawing');
  }
  return inner;
}

/** The icon at `size` px: the symbol in white on a rounded square of `colour`. */
function iconSvg(colour, size) {
  // Thin lines vanish at 16-24 px: they get thicker there.
  const stroke = size <= 24 ? 2.9 : size <= 40 ? 2.6 : 2.4;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 32 32">` +
    `<rect x="0.5" y="0.5" width="31" height="31" rx="7" fill="${colour}"/>` +
    `<g transform="translate(16 16) scale(0.8) translate(-16 -16)" fill="none" stroke="#FFFFFF"` +
    ` stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round">${glyph()}</g></svg>`
  );
}

/** RGBA pixels (and a PNG) of `svg` at `size` px, drawn by the browser. */
async function rasterise(page, svg, size) {
  return page.evaluate(
    async ({ svg, size }) => {
      const image = new Image();
      image.src = 'data:image/svg+xml;base64,' + btoa(svg);
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      const context = canvas.getContext('2d');
      context.drawImage(image, 0, 0, size, size);
      const rgba = Array.from(context.getImageData(0, 0, size, size).data);
      return { rgba, png: canvas.toDataURL('image/png').split(',')[1] };
    },
    { svg, size },
  );
}

/** A 32-bit BMP icon image: BITMAPINFOHEADER, bottom-up BGRA rows, an all-clear AND mask. */
function bmpImage(rgba, size) {
  const header = Buffer.alloc(40);
  header.writeUInt32LE(40, 0);
  header.writeInt32LE(size, 4);
  header.writeInt32LE(size * 2, 8); // colour rows + mask rows
  header.writeUInt16LE(1, 12);
  header.writeUInt16LE(32, 14);
  const pixels = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const from = (y * size + x) * 4;
      const to = ((size - 1 - y) * size + x) * 4;
      pixels[to] = rgba[from + 2];
      pixels[to + 1] = rgba[from + 1];
      pixels[to + 2] = rgba[from];
      pixels[to + 3] = rgba[from + 3];
    }
  }
  const maskRow = Math.ceil(size / 32) * 4;
  return Buffer.concat([header, pixels, Buffer.alloc(maskRow * size)]);
}

/** An .ico of the images (ICONDIR, one ICONDIRENTRY each, then the images). */
function icoFile(images) {
  const head = Buffer.alloc(6 + 16 * images.length);
  head.writeUInt16LE(0, 0);
  head.writeUInt16LE(1, 2);
  head.writeUInt16LE(images.length, 4);
  let offset = head.length;
  images.forEach(({ size, data }, index) => {
    const entry = 6 + 16 * index;
    head.writeUInt8(size >= 256 ? 0 : size, entry);
    head.writeUInt8(size >= 256 ? 0 : size, entry + 1);
    head.writeUInt16LE(1, entry + 4);
    head.writeUInt16LE(32, entry + 6);
    head.writeUInt32LE(data.length, entry + 8);
    head.writeUInt32LE(offset, entry + 12);
    offset += data.length;
  });
  return Buffer.concat([head, ...images.map((image) => image.data)]);
}

async function icon(page, colour, sizes) {
  const images = [];
  for (const size of sizes) {
    const { rgba, png } = await rasterise(page, iconSvg(colour, size), size);
    const data = size >= 256 ? Buffer.from(png, 'base64') : bmpImage(rgba, size);
    images.push({ size, data });
  }
  return icoFile(images);
}

const out = join(root, 'deploy', 'windows', 'desktop', 'icons');
mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  writeFileSync(join(out, 'arcilogger.ico'), await icon(page, TEAL, PROGRAM_SIZES));
  for (const [level, colour] of Object.entries(LEVELS)) {
    writeFileSync(join(out, `tray-${level}.ico`), await icon(page, colour, TRAY_SIZES));
  }
} finally {
  await browser.close();
}
console.log(`icons written to ${out}`);
