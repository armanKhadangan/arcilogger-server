/**
 * Per-browser preferences (theme, language, sidebar state). Storage can be unavailable
 * (private windows, locked-down Windows profiles), so every access is guarded and the UI must
 * work without it.
 */
export function readPreference(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writePreference(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // ignore: preferences are a convenience only
  }
}
