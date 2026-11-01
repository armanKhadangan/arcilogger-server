import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { QueryBar } from './query-bar';
import { CATALOGUE } from './test-data';

/** The query bar with its value kept, as the search page keeps it. */
function Bar() {
  const [value, setValue] = useState('');
  return (
    <QueryBar
      value={value}
      onChange={setValue}
      onSubmit={vi.fn()}
      onHelp={vi.fn()}
      catalogue={CATALOGUE}
      facets={undefined}
      problems={[]}
      busy={false}
    />
  );
}

describe('QueryBar', () => {
  it('keeps what is typed right after a completion, before the next frame', async () => {
    // Animation frames wait until the test lets them run (a busy browser draws late).
    const frames: FrameRequestCallback[] = [];
    const frame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    try {
      render(<Bar />);
      const box = screen.getByRole('combobox');
      await userEvent.type(box, 'kay');
      await userEvent.keyboard('{ArrowDown}{Enter}f');
      expect(box).toHaveValue('kaynak:f');
      for (const callback of frames.splice(0)) callback(performance.now());
      // The caret stays behind the typed letter, and the list follows it.
      expect((box as HTMLInputElement).selectionStart).toBe(8);
      expect(
        within(screen.getByRole('listbox'))
          .getAllByRole('option')
          .map((option) => option.textContent),
      ).toEqual(['fortigate']);
      await userEvent.keyboard('o');
      expect(box).toHaveValue('kaynak:fo');
    } finally {
      frame.mockRestore();
    }
  });
});
