# ADR-0003: Evidence integrity and immutability on Windows

- Status: accepted (2026-10-12)

## Context

The Linux design locked sealed files with `chattr +i`. Windows has no equivalent that an
administrator cannot undo: NTFS ACLs, the read-only attribute and ownership can all be changed by
an administrator.

## Decision

Integrity rests on **tamper evidence**, backed by **tamper resistance** layers:

1. **Tamper evidence (the legal proof):** SHA-256 of every file, a daily manifest chained to the
   previous day, a Kamu SM qualified time stamp on the manifest, verification right after stamping
   and on a daily/weekly/monthly schedule. Any change to a sealed file is detected.
2. **Tamper resistance on the server:** after verification the day's files get the read-only
   attribute and an NTFS ACL that denies write/delete to every account except the retention task's
   account; the services that serve the web interface only have read access.
3. **Auditing:** object-access auditing (SACL) on the vault writes every write/delete attempt to the
   Windows Security event log. ARCILOGGER will report these events on the dashboard (planned; the
   dashboard is still a placeholder in the MVP); until then they are read in the Windows Security
   log (Event Viewer).
4. **Second copy:** the sealed day is copied to a shared folder with **WORM** enabled (and
   snapshots) on a NAS or another server (`[replica] target`), whose credentials are not usable for
   deletion; hashes are verified on the copy.

## Consequences

- A local administrator could still delete files on the server, but cannot alter them without
  detection, and cannot remove the WORM copy. This is stated plainly in the auditor guide.
- The WORM feature of the replica target must be verified on site, for each installation.
