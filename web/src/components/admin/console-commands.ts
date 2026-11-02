/**
 * The server console commands the browser names (contract 2.18: these steps stay on the
 * console). An administrator runs them in an elevated prompt, so they are built only from a
 * value of the expected form: anything else the API sends (a compromised API, R7) gives no
 * command at all, never a command line of its own.
 */

const SHA256 = /^[0-9a-f]{64}$/;

/**
 * The installation's interpreter, for an administrator PowerShell: the ARCILOGGER programs are
 * not on PATH (INSTALL 10), and `-I` keeps a system Python's paths out. The commands take the
 * installation's configuration path by default.
 */
export const CONSOLE_ADMIN =
  '& "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\python.exe" -I -m arcilogger_core.admin';

/** `approve-jar <sha256>` for a staged Zamane client (D0-16); `null` for any other value. */
export function approveJarCommand(sha256: string): string | null {
  return SHA256.test(sha256) ? `${CONSOLE_ADMIN} approve-jar ${sha256}` : null;
}

/** `versions restore <n>` for a kept version (2.7.2); `null` unless `n` is a positive integer. */
export function restoreVersionCommand(n: number): string | null {
  return Number.isSafeInteger(n) && n > 0 ? `${CONSOLE_ADMIN} versions restore ${String(n)}` : null;
}
