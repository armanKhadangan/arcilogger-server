import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import '@/styles/globals.css';
import '@/lib/i18n';

import { App } from './app';
import { applyTheme, initialTheme } from './lib/theme';

// Apply the theme before the first paint (the CSP forbids an inline script in index.html).
applyTheme(initialTheme());

const container = document.getElementById('root');
if (!container) throw new Error('#root element missing');

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
