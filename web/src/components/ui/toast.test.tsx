import { act, render, screen } from '@testing-library/react';

import { Toaster, toast } from './toast';

it('shows toasts pushed from outside React', () => {
  render(<Toaster />);
  act(() => {
    toast({ title: '12.10.2026 günü mühürlendi ve doğrulandı.', variant: 'success' });
  });
  expect(screen.getByText('12.10.2026 günü mühürlendi ve doğrulandı.')).toBeInTheDocument();
});
