import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { errorResponse, installFetch, jsonResponse, makeMe, setCsrfCookie } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { EVIDENCE, need } from '@/test/samples';

const SIZE = 1000;

// jsdom has no layout; give the table's scroll container a size so the virtualiser renders rows.
const sizes = { offsetHeight: 480, offsetWidth: 1024 };
const originals = Object.fromEntries(
  Object.keys(sizes).map((key) => [
    key,
    Object.getOwnPropertyDescriptor(HTMLElement.prototype, key),
  ]),
);
beforeAll(() => {
  for (const [key, value] of Object.entries(sizes))
    Object.defineProperty(HTMLElement.prototype, key, { configurable: true, get: () => value });
});
afterAll(() => {
  for (const [key, descriptor] of Object.entries(originals))
    if (descriptor) Object.defineProperty(HTMLElement.prototype, key, descriptor);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfCookie(null);
});

/**
 * The package download as the page shows it: the bytes arrive in two parts, the drawer shows how
 * much has arrived (bar and text), then the file is offered and checked against `Repr-Digest`.
 */
it('shows the progress of a package download, then saves and checks the file', async () => {
  const ready = { ...need(EVIDENCE.details['1'], 'package 1'), size: SIZE };
  // The test sends the bytes when it wants to (the controller of the response body).
  const stream: { controller: ReadableStreamDefaultController<Uint8Array> | null } = {
    controller: null,
  };
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      stream.controller = controller;
    },
  });
  const bytes = new Uint8Array(SIZE).fill(7);
  const digest = btoa(
    String.fromCharCode(...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))),
  );
  setCsrfCookie('csrf-1');
  installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/evidence')
      return jsonResponse(200, { items: [ready], next_cursor: null });
    if (request.path === `/api/v1/evidence/${ready.id}`) return jsonResponse(200, ready);
    if (request.path === `/api/v1/evidence/${ready.id}/download`)
      return new Response(body, {
        status: 200,
        headers: {
          'Content-Type': 'application/zip',
          'Content-Length': String(SIZE),
          'Content-Disposition': `attachment; filename="${ready.file ?? 'paket.zip'}"`,
          'Repr-Digest': `sha-256=:${digest}:`,
        },
      });
    return errorResponse(404, 'not_found');
  });
  const saved: string[] = [];
  // jsdom has no object URLs: the saved file is the link's `download` name.
  Object.assign(URL, { createObjectURL: () => 'blob:paket', revokeObjectURL: () => undefined });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    saved.push(this.download);
  });

  renderApp('/evidence');
  const user = userEvent.setup();
  await user.click(await screen.findByText(ready.package_name ?? '-'));
  const sheet = await screen.findByRole('dialog', { name: ready.package_name ?? '' });
  await within(sheet).findByText('Boyut: 1.000 B');
  await user.click(within(sheet).getByRole('button', { name: 'İndir' }));

  await waitFor(() => {
    expect(stream.controller).not.toBeNull();
  });
  stream.controller?.enqueue(bytes.slice(0, 400));
  const bar = await within(sheet).findByRole('progressbar', { name: 'İndirme ilerlemesi' });
  await within(sheet).findByText('400 B / 1.000 B alındı (%40)');
  expect(bar).toHaveAttribute('max', String(SIZE));
  expect(bar).toHaveAttribute('value', '400');
  // Busy, not `disabled`: a focused button keeps its focus while the file arrives.
  expect(within(sheet).getByRole('button', { name: 'İndiriliyor…' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );

  stream.controller?.enqueue(bytes.slice(400));
  stream.controller?.close();
  await within(sheet).findByText(/eşleşiyor/);
  expect(within(sheet).queryByRole('progressbar')).toBeNull();
  expect(saved).toEqual([ready.file]);
  click.mockRestore();
});
