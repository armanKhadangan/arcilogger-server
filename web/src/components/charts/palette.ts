/**
 * Chart colours, in fixed order (never cycled). Validated for colour-vision deficiency and
 * contrast on both the light and the dark surface; see docs/DESIGN-TOKENS.md.
 * A series beyond the seventh folds into "Diğer" (CHART_OTHER).
 */
export const CHART_COLORS = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
  'var(--chart-6)',
  'var(--chart-7)',
] as const;

export const CHART_OTHER = 'var(--chart-other)';

export function seriesColor(index: number): string {
  return CHART_COLORS[index] ?? CHART_OTHER;
}

/** Colour of a part-to-whole slice; the folded "Diğer" slice is always neutral. */
export function sliceColor(slice: { other?: boolean }, index: number): string {
  return slice.other ? CHART_OTHER : seriesColor(index);
}
