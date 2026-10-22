import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import type { MutableRefObject, ReactNode, RefObject } from 'react';

/**
 * Focus return for dialogs and drawers (WCAG 2.4.3 focus order).
 *
 * Radix gives the focus back only to a `Dialog.Trigger`. Ours are mostly opened from state — a
 * table row, a menu item, the answer of a request — and without this the focus fell to `<body>`
 * on close, so keyboard and screen-reader users lost their place after every action.
 *
 * By default the element that had the focus when the dialog opened gets it back. That element is
 * read while the dialog's content renders, i.e. before `autoFocus` inside the dialog moves the
 * focus. `returnFocus` overrides it where that element is gone by then: a menu item (the menu is
 * already closed — use the menu's trigger button), or a dialog that follows another one (a
 * temporary password after "Kullanıcı ekle").
 */
export interface ReturnFocusProps {
  returnFocus?: RefObject<HTMLElement | null> | undefined;
}

function focusedElement(): HTMLElement | null {
  const element = document.activeElement;
  return element instanceof HTMLElement && element !== document.body ? element : null;
}

/**
 * Focuses `element`, or — when the table re-rendered it meanwhile as a new node — the element that
 * now carries its id. When it cannot take the focus (a submit button disabled while the request
 * that the re-authentication dialog allowed is running), the dialog around it gets the focus, so
 * it stays inside that dialog. `false` when there is nothing (left) to focus.
 */
export function focusBack(element: HTMLElement | null | undefined): boolean {
  if (!element) return false;
  const target = element.isConnected
    ? element
    : element.id
      ? document.getElementById(element.id)
      : null;
  if (!target) return false;
  target.focus();
  if (document.activeElement === target) return true;
  const dialog = target.closest<HTMLElement>('[role="dialog"], [role="alertdialog"]');
  if (!dialog) return false;
  dialog.focus();
  return document.activeElement === dialog;
}

function FocusOrigin({ into }: { into: MutableRefObject<HTMLElement | null> }) {
  const [origin] = useState(focusedElement);
  useLayoutEffect(() => {
    into.current = origin;
  }, [into, origin]);
  return null;
}

/**
 * For a dialog content wrapper: `origin` goes first inside the Radix content (it notes the opener
 * each time the dialog opens), `onCloseAutoFocus` replaces the caller's handler of that name.
 */
export function useReturnFocus(
  returnFocus: RefObject<HTMLElement | null> | undefined,
  onCloseAutoFocus: ((event: Event) => void) | undefined,
): { origin: ReactNode; onCloseAutoFocus: (event: Event) => void } {
  const opener = useRef<HTMLElement | null>(null);
  const handleClose = useCallback(
    (event: Event) => {
      onCloseAutoFocus?.(event);
      if (event.defaultPrevented) return;
      if (focusBack(returnFocus?.current) || focusBack(opener.current)) event.preventDefault();
    },
    [onCloseAutoFocus, returnFocus],
  );
  return { origin: <FocusOrigin into={opener} />, onCloseAutoFocus: handleClose };
}
