# ADR-0006: Standalone verifier — one file, Python standard library only, clean-room

- Status: accepted (2026-10-12); amended after review (2026-10-12): package completeness, names,
  stamp timing, stricter cryptography, builder self-check
- Deciders: the product owner; proposed with milestone M3 (evidence package, build specification
  9.6)

## Context

An evidence package ("Kanıt Paketi") goes to people outside the company: a prosecutor's expert, a
court, an auditor. They must be able to check it **without ARCILOGGER, without our server and
without trusting us**. The specification asks for a verifier with "no dependencies beyond Python 3
standard library + bundled pure-Python ASN.1/CMS code, or a static binary".

The recipient's computer is unknown: often Windows, sometimes without internet, often with an old
or no Python, never with our virtual environment. Whatever we ship must be small enough to read.

## Decision

1. The verifier is **one file**, `core/src/arcilogger_core/evidence/standalone/verify.py`, that
   uses **only the Python 3 standard library** and runs on **Python 3.9 or newer**. It is copied
   unchanged into every package next to two thin wrappers (`verify.ps1`, `verify.sh`) that find a
   Python interpreter and run `python -I -S verify.py`. `-I -S` isolates it from installed
   packages and `PYTHON*` variables, so a passing run proves it needs nothing else.
2. It is a **clean-room implementation** written from the public standards only: ITU-T X.690
   (DER, and BER indefinite lengths for robustness), RFC 5280 (certificates, CRLs), RFC 5652 (CMS),
   RFC 3161 + RFC 5816 (TSTInfo, ESSCertIDv2), RFC 2634 (ESSCertID), RFC 8017 (RSA), FIPS 186-4 /
   SEC 1 (ECDSA), RFC 8785 (canonical JSON). No code was copied from any other project (in
   particular not from the licence-less `kamusm-go`).
3. Supported algorithms — those RFC 3161 tokens use in practice:
   - RSA PKCS #1 v1.5 and RSASSA-PSS (parameters read from the AlgorithmIdentifier);
   - ECDSA on P-256, P-384, P-521 (uncompressed and compressed points), in plain Python
     arithmetic (Jacobian coordinates; verification only, so constant time is not needed);
   - SHA-256/384/512 for everything a time stamp binds: the messageImprint and the SignerInfo
     digest (and the RSASSA-PSS hash) must be SHA-256 or stronger, otherwise `token-weak-digest`;
     SHA-1 and SHA-224 are read only where a standard or an older CA needs them: ESSCertID v1
     (RFC 2634 defines it with SHA-1) and certificate/CRL signatures in the chain.
4. It checks exactly what ARCILOGGER's own verifier (`arcilogger_core.tsa.verify`, built on
   `cryptography`/`asn1crypto`) checks, in the same order, and the tests prove that **both reach
   the same verdict** on the same tokens: valid RSA, RSA-PSS and ECDSA tokens, bare tokens, BER
   encodings, and every failure (imprint, signature, signed attributes, ESS binding, EKU, chain,
   validity at genTime, missing certificate, rejected or garbage responses, future genTime).
   Both verifiers apply the same rules (since the final MVP review): signed attributes that
   appear twice (RFC 5652 11.1/11.2) are rejected; a SHA-1 (or SHA-224) imprint or SignerInfo
   digest is rejected (`token-weak-digest`); the TSA certificate must allow signing
   (digitalSignature/nonRepudiation) and every issuer must allow keyCertSign when the extension is
   present, within its pathLenConstraint; an ESSCertID issuerSerial must name the TSA certificate.
   A differential test feeds the same tokens to both (`test_core_and_standalone_verifiers_agree`),
   so a day accepted at sealing can always be packaged. The only remaining difference is DER
   strictness: the standalone verifier rejects a constructed encoding of a primitive type and a
   non-minimal INTEGER, and an ECDSA signature verifies only in its one DER encoding (as OpenSSL
   and `cryptography` do; a differential test of 3,096 signatures against `cryptography` agrees
   in every case). Because the builder runs the standalone checks on every finished package
   (decision 7), a token on which the two verifiers disagree can never be shipped.
   A non-critical EKU extension is an error in both unless the site set
   `tsa.require_critical_eku = false` (K6); that choice is sealed into each manifest and both
   verifiers then report it as a warning for those days only.
5. On top of the token it checks the package: every file name (safe and distinct as Windows sees
   names — `..`, absolute paths, trailing dots or spaces, device names, control characters and
   case-only collisions make the package unreadable, exit code 2, because what is verified must be
   what an archive tool unpacks); `paket.json` (present, canonical, size and SHA-256 of each file,
   no unlisted file, every day of its scope present); every manifest (canonical JSON, no duplicate
   keys) and every file it lists (size, SHA-256, decompressed SHA-256 and line count); **no file in
   a sealed day folder that the manifest does not list** (`file-not-sealed`, a failure even in a
   vault copy) and, in a package, no file that is not a known part; when each day was stamped,
   computed from the verified token (more than 48 hours after the end of the day is late whatever
   the manifest says; a stamp before the day ended or far from `created_at` is reported); the chain
   between days ("chain starts in this package", and warnings when a day in the middle is missing
   or the chain restarts); deletion records and the chain anchor; renewal stamps; CRLs contained in
   the package (offline); and the extract (byte-for-byte equal to the sealed lines). A package
   without any sealed day fails (`nothing-verified`). Output: Turkish lines with English in
   brackets, a summary that repeats the root fingerprints, stable finding codes, `--json`; exit
   code 0 only if no check failed (warnings are printed in the verdict line).
6. ARCILOGGER itself (`arcilogger-core evidence verify`) always runs **its own** copy of
   `verify.py` in an isolated subprocess, never the copy inside a package, which could have been
   altered.
7. The builder never hands out a package its own verifier rejects: after writing the ZIP it runs
   the standalone checks (quick mode) on the ZIP read back through `pyzipper` (so also when it is
   encrypted) and deletes it on any failure. It also refuses a vault whose included days are not
   chained as sealed.
8. Two modes. An **evidence package** (a ZIP, a folder with `vault/`, `paket.json`, `verify.py` or
   the guide) must be complete: a missing index, an unlisted file or an unknown file is a failure. A
   **copy of the vault** (for example the replica on a NAS: no index, day folders at the top) is
   verified for its sealed days and records; unknown files outside the day folders are warnings
   there.

## Alternatives considered

| Option | Why not |
|---|---|
| Bundle `asn1crypto` + `oscrypto`/`cryptography` | Several files and native code (`cryptography`) to ship and trust; version drift; harder to read |
| A static Go/Rust binary | Needs a separate toolchain and signing; an auditor cannot read it; Windows SmartScreen warnings |
| Only `openssl ts -verify` | Not installed on Windows by default; checks only the token, not the package, chain or extract — kept as method C in `DOGRULAMA.txt`, together with `sha256sum`/`Get-FileHash`, chain and extract commands, so a recipient can check everything without `verify.py` |
| Only Kamu SM's Zamane client | Needs Java 1.8, closed source, output format undocumented — kept as the official cross-check |

## Limits (stated in DOGRULAMA.txt and the auditor guide)

- **No online revocation checking:** no CRL download, no OCSP. CRLs inside the package are
  checked offline: every usable CRL counts (an entry at or before genTime fails, a later one or
  certificateHold warns, removeFromCRL cancels), and "not revoked" is only reported from a CRL
  issued after genTime (`crl-stale` otherwise). ARCILOGGER does not download Kamu SM CRLs yet
  (their addresses are an open point, `docs/kamusm.md` K10), so packages currently say "no CRL".
- No full RFC 5280 path validation: basicConstraints, keyUsage and pathLenConstraint are checked,
  but not certificate policies or name constraints; the chain must end at a root shipped in the
  package or given with `--kokler`. Kamu SM's certificate profile must be confirmed on site (K6).
- It cannot prove that a root certificate belongs to Kamu SM. It prints SHA-256 and SHA-1
  fingerprints to compare with Kamu SM's published values, and `--kokler` lets the auditor trust
  only roots they downloaded themselves.
- Only NIST prime curves and RSA; Ed25519/Ed448 or GOST signatures are reported as unsupported.
- A modified `verify.py` could lie, and the copy inside a package cannot vouch for itself (its
  hash in `paket.json` is in the same package). `evidence build` prints its SHA-256 and
  `PackageInfo.verifier_sha256` carries it, so the API can show it on the download page, record it
  in the audit log and put it in the PDF report; the recipient must get it through a channel
  separate from the package, or run their own copy. No publication channel exists yet (owner
  decision). Until then the guide sends recipients to method C (OpenSSL and hash tools), which
  checks stamps, files, chain and extract without `verify.py`.
- Resource limits: metadata files (index, manifests, tokens, certificates, CRLs, extracts) are read
  only up to 64 MiB, one raw line up to 16 MiB, DER nesting up to 64 levels; an extract of a sealed
  file that did not verify is not read. `extract_archive` (used by `evidence verify` for encrypted
  packages) refuses unsafe names and more than the free space or 256 GiB.
- Lateness uses a fixed 48-hour limit (build specification 6.8, the default of
  `seal.late_after_hours`); the manifest does not record the configured value.

## Consequences

- About 3,300 lines of auditable Python, kept clean for ruff and `mypy --strict` like the rest of
  core, tested in-process (coverage) and in an isolated subprocess on every Python available in
  CI (3.9–3.13 where installed).
- When Kamu SM's real token format is known (K1, K5, K6), the verifier gets regression fixtures
  from the test server like the core verifier.
- `VERIFIER_VERSION` (in the file and in `paket.json`) changes whenever its checks change.
