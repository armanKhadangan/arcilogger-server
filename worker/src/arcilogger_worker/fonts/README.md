# Rapor yazı tipleri / Report fonts

The official 5651 PDF report (`arcilogger_api.reports`) and the scheduled usage reports
(`arcilogger_worker.reports`, ADR-0012) embed these fonts, registered once by
`arcilogger_worker.pdf.fonts`; nothing is loaded from the system or the network, so a report looks
the same — and hashes the same — on every server. (Until ADR-0012 the files were in
`arcilogger_api/fonts/`; they moved unchanged.)

| File | Used for | SHA-256 |
|---|---|---|
| `DejaVuSans.ttf` | text | `7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954` |
| `DejaVuSans-Bold.ttf` | headings, labels | `e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724` |
| `DejaVuSansMono.ttf` | hashes, raw log lines, paths | `b4a6c3e4faab8773f4ff761d56451646409f29abedd68f05d38c2df667d3c582` |
| `LICENSE-DejaVu.txt` | licence text (copied unchanged) | `7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93` |

## Source

- npm registry package **`dejavu-fonts-ttf` 2.37.3** (DejaVu fonts 2.37, released 2016;
  repository `senotrusov/dejavu-fonts-ttf`), downloaded 2026-10-12 from
  `https://registry.npmjs.org/dejavu-fonts-ttf/-/dejavu-fonts-ttf-2.37.3.tgz`.
- Tarball checked against the registry metadata before use:
  `sha512-f1hd7jJbeQa1VWcw+K2KrTXS50zTMaHpVC4XIKJpNcDeYR5ajMtj/iLlQDYNvLOKamUB3ARVVCf79lNwNVztSQ==`
  (npm `integrity`), SHA-1 `23f2bf5af594ca2a8a3a53224224b03467a6e0b8` (npm `shasum`);
  SHA-256 of the tarball `b7013ac58f9225250619db82cfb72e99c37ca8195f2b5a06a9e6a95bc5e1c84a`.
- Files taken unchanged from `package/ttf/` (the three fonts) and `package/LICENSE`.
- The package's `langcover.txt` lists Turkish (`tr`) at 100 % for DejaVu Sans and Sans Mono;
  `api/tests/test_report_fonts.py` checks every Turkish letter against the glyph table and the
  checksums above against the files.

## Licence

Bitstream Vera Fonts licence (Bitstream, Inc.) with the Arev Fonts licence (Tavmjong Bah); the
DejaVu changes are in the public domain — see `LICENSE-DejaVu.txt`. It permits use, copying,
redistribution and embedding, also as part of a larger (commercial) package, provided the
copyright and licence notice stays with the fonts; modified fonts must be renamed. ARCILOGGER does
not modify the fonts (reportlab embeds **subsets** in each PDF, which the licence allows as use of
the Font Software; the font names in a PDF carry reportlab's subset prefix).
