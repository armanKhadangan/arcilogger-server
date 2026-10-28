import { getConfig, screen } from '@testing-library/react';

/** The dialog on screen whose title (`aria-labelledby`) is `name`, if there is one. */
function dialogNamed(name: string): HTMLElement | null {
  for (const dialog of document.querySelectorAll<HTMLElement>('[role="dialog"]')) {
    const title = document.getElementById(dialog.getAttribute('aria-labelledby') ?? '');
    if (title?.textContent === name) return dialog;
  }
  return null;
}

/**
 * What `read` finds in the first frame with the dialog `name` on screen after `open` (render the
 * app, click an opener): a MutationObserver runs after each commit, before React's next scheduled
 * work (an effect's state update) can change it. A dialog that fills its form in an effect shows
 * an empty or the previous form first, and a test reading it right after `findByRole` fails on a
 * slow runner.
 *
 * `open` runs outside the act() environment, as RTL's own `waitFor` does: inside act(), React
 * would flush the effect's update before the observer sees the frame, which a browser commits.
 */
export async function firstFrame<T>(
  name: string,
  open: () => void,
  read: (dialog: HTMLElement) => T,
): Promise<T[]> {
  const seen: T[] = [];
  const observer = new MutationObserver(() => {
    const dialog = dialogNamed(name);
    if (dialog !== null && seen.length === 0) seen.push(read(dialog));
  });
  observer.observe(document.body, { childList: true, subtree: true });
  try {
    await getConfig().asyncWrapper(async () => {
      open();
      await screen.findByRole('dialog', { name });
    });
  } finally {
    observer.disconnect();
  }
  return seen;
}
