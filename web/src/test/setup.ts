import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';

import '@/lib/i18n';

// A busy machine (parallel test workers) can take longer than the default 1 s to render a page;
// the pages themselves are preloaded per test file (`src/test/render.tsx`).
configure({ asyncUtilTimeout: 5_000 });

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

// jsdom lacks these browser APIs used by Radix, TanStack Virtual and theme detection.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

function polyfill(target: object, name: string, value: unknown): void {
  if (!(name in target)) {
    Object.defineProperty(target, name, { value, writable: true, configurable: true });
  }
}

polyfill(window, 'ResizeObserver', ResizeObserverStub);
polyfill(window, 'matchMedia', (query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  addListener: () => undefined,
  removeListener: () => undefined,
  dispatchEvent: () => false,
}));
polyfill(Element.prototype, 'scrollIntoView', () => undefined);
polyfill(Element.prototype, 'hasPointerCapture', () => false);
polyfill(Element.prototype, 'releasePointerCapture', () => undefined);
