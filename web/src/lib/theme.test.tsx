import { act, render, screen } from '@testing-library/react';

import { ThemeProvider, useTheme } from './theme';

function Probe() {
  const { preference, resolved, setPreference } = useTheme();
  return (
    <button
      type="button"
      onClick={() => {
        setPreference('dark');
      }}
    >
      {preference}/{resolved}
    </button>
  );
}

it('switches to dark, sets the class on <html> and remembers the choice', () => {
  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  expect(screen.getByRole('button')).toHaveTextContent('system/light');
  act(() => {
    screen.getByRole('button').click();
  });
  expect(screen.getByRole('button')).toHaveTextContent('dark/dark');
  expect(document.documentElement).toHaveClass('dark');
  expect(window.localStorage.getItem('arci.theme')).toBe('dark');
});
