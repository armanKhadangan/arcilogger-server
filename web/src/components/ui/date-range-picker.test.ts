import { presetRange } from './date-range-picker';

describe('presetRange', () => {
  const now = new Date(2026, 9, 12, 18, 19, 7);

  it('resolves presets against the given moment', () => {
    expect(presetRange('today', now).from).toEqual(new Date(2026, 9, 12));
    expect(presetRange('yesterday', now)).toEqual({
      from: new Date(2026, 9, 11),
      to: new Date(2026, 9, 11, 23, 59, 59, 999),
    });
    expect(presetRange('last7', now).from).toEqual(new Date(2026, 9, 6));
    expect(presetRange('last30', now).from).toEqual(new Date(2026, 8, 13));
    expect(presetRange('thisMonth', now).from).toEqual(new Date(2026, 9, 1));
    expect(presetRange('thisMonth', now).to).toEqual(new Date(2026, 9, 12, 23, 59, 59, 999));
  });
});
