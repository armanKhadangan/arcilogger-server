# Bağımsız doğrulayıcı / Standalone verifier

Kanıt paketlerindeki `verify.py` dosyasının kaynağı burada değil, çekirdek pakettedir:

The source of the `verify.py` shipped in every evidence package lives in the core package:

| File | Purpose |
|---|---|
| [`core/src/arcilogger_core/evidence/standalone/verify.py`](../../core/src/arcilogger_core/evidence/standalone/verify.py) | The verifier: one file, Python 3.9+ standard library only |
| [`core/src/arcilogger_core/evidence/standalone/verify.ps1`](../../core/src/arcilogger_core/evidence/standalone/verify.ps1) | Windows wrapper (PowerShell 5.1 / 7), ASCII + CRLF |
| [`core/src/arcilogger_core/evidence/standalone/verify.sh`](../../core/src/arcilogger_core/evidence/standalone/verify.sh) | Linux / macOS wrapper |

## Nasıl dağıtılır? / How it is shipped

- `arcilogger-core evidence build` and the web interface's evidence packages (the same builder)
  copy the three files **byte for byte** to the root of every package and list their SHA-256 in
  `paket.json`.
- `arcilogger-core evidence verify PATH` runs the installed copy (never the one inside a package)
  as `python -I -S verify.py PATH`.
- It is importable as `arcilogger_core.evidence.standalone.verify`, so the tests exercise exactly
  the file an auditor receives (`core/tests/test_evidence*.py`).

## Elle çalıştırma / Running it by hand

```bash
python -I -S core/src/arcilogger_core/evidence/standalone/verify.py <paket klasörü | şifresiz ZIP>
python -I -S verify.py --kokler <Kamu SM kökleri klasörü> <kasa kopyası>   # e.g. the NAS replica
```

Exit code: 0 verified, 1 a check failed, 2 the package cannot be read. `--json` prints the findings
for programs. See [docs/DENETCI-KILAVUZU.md](../../docs/DENETCI-KILAVUZU.md) and
[ADR-0006](../../docs/adr/0006-standalone-verifier.md).

Verifier version 4 (final MVP review): lines are counted as ARCILOGGER seals them (a non-empty
last line without `\n`, `unterminated_last_line` in the manifest, counts); a package without
ARCILOGGER's audit log reports it as "not included" (`audit-log-excluded`, `scope.audit_log`); a
manifest that lists a run of empty days (`unsealed_empty_days` in its gaps) may link past them;
a non-critical EKU is a warning only for days whose sealed manifest says
`tsa.require_critical_eku = false`; a legal-grade claim on ARCILOGGER's test root fails.

## Özet / Its hash

The `verify.py` inside a package cannot vouch for itself. `arcilogger-core evidence build` prints
its SHA-256 (also in `PackageInfo.verifier_sha256` for the API, the audit record and the PDF
report): give it to the recipient **through a channel separate from the package** (handover
record, official letter). A per-release publication channel does not exist yet; that is an owner
decision. Recipients who cannot confirm the hash use method C of `DOGRULAMA.txt` (OpenSSL and hash
tools only). To compute it:

```powershell
Get-FileHash -Algorithm SHA256 core\src\arcilogger_core\evidence\standalone\verify.py
```
