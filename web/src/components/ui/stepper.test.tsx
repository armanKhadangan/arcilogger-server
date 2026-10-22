import { render, screen } from '@testing-library/react';

import { Stepper } from './stepper';

it('marks the current step for assistive technology', () => {
  render(
    <Stepper
      label="Kurulum"
      current={1}
      steps={[
        { id: 'a', title: 'Yönetici' },
        { id: 'b', title: 'Site' },
        { id: 'c', title: 'Kaynaklar' },
      ]}
    />,
  );
  const steps = screen.getAllByRole('listitem');
  expect(steps[1]).toHaveAttribute('aria-current', 'step');
  expect(steps[0]).not.toHaveAttribute('aria-current');
  expect(screen.getByRole('list', { name: 'Kurulum' })).toBeInTheDocument();
});

it('reads a finished step as "Tamamlandı", not as the "Bitir" button', () => {
  render(
    <Stepper
      label="Paket oluşturma adımları"
      current={1}
      steps={[
        { id: 'scope', title: 'Kapsam' },
        { id: 'content', title: 'İçerik' },
      ]}
    />,
  );
  const [done, current] = screen.getAllByRole('listitem');
  expect(done).toHaveTextContent('TamamlandıKapsam');
  expect(done).not.toHaveTextContent('Bitir');
  expect(current).not.toHaveTextContent('Tamamlandı');
});
