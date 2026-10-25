# Tasarım belirteçleri / Design tokens

Source of truth: `web/src/styles/globals.css`. The UI kit page (`/ui-kit`; dev server and E2E
build only, never the shipped bundle) shows every token and component in both themes. All values
follow the build specification (section 12.1) except the documented additions below.

## Additions and why

| Token | Light | Dark | Reason |
|---|---|---|---|
| `--muted-strong` | `#475569` | `#94A3B8` | Secondary text on the tinted `--subtle` background; `--muted` there is 4.3:1, below WCAG AA |
| `--*-ink` (primary, accent, success, warning, danger, info) | 800-level hues | 300-level hues | Text on the matching `*-soft` background (badges); the base status colours are too light for small text |
| `--danger-solid` / `--danger-solid-hover` | `#DC2626` / `#B91C1C` | same | Destructive buttons with white text in both themes (4.8:1) |
| `--primary-foreground` (dark) | — | `#042F2E` | White on teal `#14B8A6` is 2.5:1; dark text is 5.8:1 |
| `--chart-1` | `#0D9488` | `#0D9488` | Spec's first chart colour `#0F766E` failed the chroma check (reads as grey) |
| `--chart-other` | `#64748B` | `#64748B` | Slate is reserved for the folded "Diğer" series, never a regular series |
| `--heat-0..5` | `#F1F5F9`, `#14B8A6` → `#042F2E` | `#1A2332`, `#0F766E` → `#5EEAD4` | Sequential ramp for heatmaps (step 0 = no activity) |

## Checked contrast (WCAG 2.1)

| Text / background | Ratio |
|---|---|
| `--muted` on `--surface` (light) | 4.76 |
| `--muted` on `--background` (light) | 4.55 |
| `--muted-strong` on `--subtle` (light) | ≈ 7.0 |
| White on `--primary` (light) | 5.47 |
| `--primary-foreground` on `--primary` (dark) | 5.81 |
| Sidebar text `#CBD5E1` on `#0F172A` | 12.0 |
| Badge inks on soft backgrounds (light) | 6.4 – 7.2 |
| Badge inks on soft backgrounds (dark) | 8.2 – 10.6 |

Playwright runs axe (WCAG 2.1 A/AA) on the UI kit in light and dark mode on every CI run.

## Chart palette validation

Categorical order (fixed, never cycled): `#0D9488 #2563EB #D97706 #7C3AED #DB2777 #0891B2 #65A30D`
(+ `#64748B` for "Diğer").

| Check | Light (on `#FFFFFF`) | Dark (on `#111827`) |
|---|---|---|
| Lightness band | pass | pass |
| Chroma floor (≥ 0.10) | pass | pass |
| CVD separation, adjacent (deutan/protan ΔE ≥ 8) | pass, worst 10.3 | pass, worst 10.3 |
| Normal-vision floor (ΔE ≥ 15) | pass, worst 21.1 | pass, worst 21.1 |
| Contrast vs surface (≥ 3:1) | pass | pass |

Heat ramps: monotone lightness, adjacent ΔL ≥ 0.06, light end ≥ 2:1 against the surface — pass in
both themes.

Charts never carry information by colour alone: every chart has a legend with values and a table
view, status always comes with an icon and a label.
