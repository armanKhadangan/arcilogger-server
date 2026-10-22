import { render, screen } from '@testing-library/react';

import { Heatmap, heatStep } from './heatmap';

describe('heatStep', () => {
  it('maps zero to the empty step and the rest to 1..5', () => {
    expect(heatStep(0, 60)).toBe(0);
    expect(heatStep(-1, 60)).toBe(0);
    expect(heatStep(1, 60)).toBe(1);
    expect(heatStep(12, 60)).toBe(1);
    expect(heatStep(13, 60)).toBe(2);
    expect(heatStep(60, 60)).toBe(5);
    expect(heatStep(600, 60)).toBe(5);
    expect(heatStep(5, 0)).toBe(0);
  });
});

describe('Heatmap', () => {
  it('labels every cell with its row, hour and value', () => {
    render(
      <Heatmap
        caption="Aktif dakika"
        rows={[{ key: 'd1', label: '12.10', values: [0, 30, ...Array<number>(22).fill(0)] }]}
        max={60}
        formatValue={(value) => `${value} dk`}
      />,
    );
    expect(screen.getByRole('table', { name: 'Aktif dakika' })).toBeInTheDocument();
    expect(screen.getByLabelText('12.10 · 01:00 · 30 dk')).toHaveClass('bg-heat-3');
    expect(screen.getByLabelText('12.10 · 00:00 · Etkinlik yok')).toHaveClass('bg-heat-0');
  });
});
