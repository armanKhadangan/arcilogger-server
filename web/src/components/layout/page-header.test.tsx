import { render } from '@testing-library/react';

import { PageHeader } from './page-header';

describe('PageHeader', () => {
  it('names the browser tab after the page, and gives the product’s name back on leaving', () => {
    const page = render(<PageHeader title="Mühürleme" />);
    expect(document.title).toBe('Mühürleme · ARCILOGGER');
    page.rerender(<PageHeader title="Kanıt Paketleri" />);
    expect(document.title).toBe('Kanıt Paketleri · ARCILOGGER');
    page.unmount();
    expect(document.title).toBe('ARCILOGGER');
  });
});
