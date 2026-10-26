"""``arcilogger-core`` command line.

Windows Task Scheduler runs these commands for the evidence jobs; none of them needs the database
or the web application::

    arcilogger-core seal [--day YYYY-MM-DD]          seal closed days (default: everything due)
    arcilogger-core verify [--day D | --last N | --all] [--deep] [--json]
    arcilogger-core replica [--day D] [--test]       copy sealed days to the second location
    arcilogger-core retention [--dry-run]            delete days past retention (if enabled)
    arcilogger-core credit                           Kamu SM credit (kontör)
    arcilogger-core clock-check                      SNTP offset sample
    arcilogger-core tsa-test                         stamp and verify a random 1 KB file
    arcilogger-core requests run                     the browser administration's signed
                                                     requests: credit query, TSA test,
                                                     replica test (ADR-0014)
    arcilogger-core secret init|set|list|delete      encrypted credentials (Kamu SM, database,
                                                     replica, SMTP)
    arcilogger-core status                           summary for monitoring
    arcilogger-core config-check [--json]            what a production configuration still
                                                     lacks (organisation name, time source)
    arcilogger-core evidence build --from D --to D [--source S …] [--extract FILE.json]
                    [--password] [--out DIR] [--pdf FILE] [--include-audit-log]
                                                     evidence package (Kanıt Paketi)
    arcilogger-core evidence verify PATH [--quick] [--json]  standalone verifier, no config
    arcilogger-core evidence purge [--keep-days 30]  delete old packages from exports/
    arcilogger-core version | info

``seal`` also runs the browser administration's signed seal requests ("Şimdi mühürle"). Every
command refuses a configuration whose ``secrets_dir`` or ``data_dir`` is not the installation's
own folder (:func:`arcilogger_core.config.check_fixed_location`).

Exit codes: 0 success, 1 a problem that needs attention, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import platform
import secrets as pysecrets
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from functools import cached_property
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from arcilogger_core import PRODUCT_NAME, __version__
from arcilogger_core.admin.contract import (
    AdminLayout,
    CreditResult,
    ReplicaTestResult,
    SealResult,
    TaskKind,
    TaskRequest,
    TaskResult,
    TsaTestResult,
)
from arcilogger_core.alerts import raise_alert
from arcilogger_core.chain import sealed_days, verify_range
from arcilogger_core.clockcheck import check_clock
from arcilogger_core.config import (
    Config,
    ConfigLocationError,
    check_fixed_location,
    default_config_path,
    load_config,
    setup_problems,
)
from arcilogger_core.credit import check_credit
from arcilogger_core.evidence import (
    DEFAULT_KEEP_DAYS,
    EvidenceError,
    build_package,
    extract_archive,
    is_encrypted,
    load_refs,
    package_days,
    purge_expired,
    run_verifier,
)
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.fmt import format_bytes, format_datetime
from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.layout import Layout
from arcilogger_core.replica import probe, replicate_day, replicate_due
from arcilogger_core.retention import run_retention
from arcilogger_core.seal import DELETED, SEALED, SKIPPED, Sealer, SealOutcome, config_alert
from arcilogger_core.secretstore import (
    DATABASE_PASSWORD,
    KAMUSM_CUSTOMER,
    KAMUSM_PASSWORD,
    REPLICA_PASSWORD,
    SMTP_PASSWORD,
    USAGE_PSEUDONYM_KEY,
    SecretStore,
    SecretStoreError,
    record_change,
)
from arcilogger_core.smbshare import connected, unc_root
from arcilogger_core.taskrequests import clean, finish, take, task_result
from arcilogger_core.timeutil import SITE_TZ_NAME, isoformat_site, site_day, utc_now
from arcilogger_core.tsa import TsaExecutableError, make_provider, trusted_roots
from arcilogger_core.tsa.base import TsaConfigurationError, TsaError
from arcilogger_core.tsa.verify import confirmed_legal_grade, verify_token

SECRET_NAMES = (
    KAMUSM_CUSTOMER,
    KAMUSM_PASSWORD,
    DATABASE_PASSWORD,
    REPLICA_PASSWORD,
    SMTP_PASSWORD,
    USAGE_PSEUDONYM_KEY,
)
log = logging.getLogger("arcilogger.cli")


class UsageError(Exception):
    pass


class SealConfigError(Exception):
    """Sealing cannot start: ``cause`` is a key of :data:`arcilogger_core.seal.CONFIG_CAUSES`."""

    def __init__(self, cause: str, error: BaseException) -> None:
        super().__init__(str(error))
        self.cause = cause
        self.error = error


class App:
    """Lazily built context for one command run."""

    def __init__(self, config_path: Path | None) -> None:
        self.config_path = config_path or default_config_path()
        self.config: Config = load_config(self.config_path)
        check_fixed_location(self.config, self.config_path)
        self.layout = Layout(self.config.data_dir)
        self.secrets = SecretStore(
            self.config.secrets_dir / "secret.key", self.config.secrets_dir / "secrets.json"
        )

    @cached_property
    def anchors(self) -> TrustAnchors:
        """What the executable policy trusts: the folder of the fixed configuration path and
        its ``admin\\policy.json``, read once per run (ADR-0014, Day 0 note D0-2)."""
        return TrustAnchors.of(self.config_path)

    @property
    def keys_dir(self) -> Path:
        """``admin\\keys`` of the installation folder: they verify the task requests."""
        return AdminLayout(self.anchors.root / "admin").keys

    def provider(self) -> Any:
        return make_provider(self.config, self.layout, self.secrets, anchors=self.anchors)

    def roots(self) -> Any:
        return trusted_roots(self.config, self.layout)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}") from exc


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


# -- commands -------------------------------------------------------------------------------------


def cmd_info(_: argparse.Namespace) -> int:
    now = utc_now()
    print(
        "\n".join(
            [
                f"{PRODUCT_NAME} core {__version__}",
                f"Python      : {platform.python_version()} ({sys.executable})",
                f"Platform    : {platform.platform()}",
                f"Time zone   : {SITE_TZ_NAME}",
                f"UTC now     : {now.isoformat(timespec='seconds')}",
                f"Site now    : {isoformat_site(now, 'seconds')}  ({format_datetime(now)})",
                f"Config      : {default_config_path()}",
            ]
        )
    )
    return 0


def _provider_cause(app: App) -> str:
    """Why :func:`arcilogger_core.tsa.make_provider` refused the configuration."""
    tsa = app.config.tsa
    if tsa.provider != "zamane":
        return "provider"
    if tsa.zamane_jar is None:
        return "zamane-jar"
    try:
        stored = set(app.secrets.names())
    except (OSError, ValueError, SecretStoreError):
        return "secret-store"
    return "credentials" if not {KAMUSM_CUSTOMER, KAMUSM_PASSWORD} <= stored else "provider"


def _sealer(app: App) -> Sealer:
    """The sealer, or :class:`SealConfigError` when sealing cannot start at all."""
    try:
        provider = app.provider()
    except SecretStoreError as exc:
        raise SealConfigError("secret-store", exc) from exc
    except TsaExecutableError as exc:
        raise SealConfigError("executable", exc) from exc
    except TsaConfigurationError as exc:
        raise SealConfigError(_provider_cause(app), exc) from exc
    try:
        roots = app.roots()
    except TsaConfigurationError as exc:
        raise SealConfigError("roots", exc) from exc
    return Sealer(app.config, provider=provider, roots=roots)


def cmd_seal(args: argparse.Namespace, app: App) -> int:
    # The browser's seal requests ("Şimdi mühürle"); a run for one --day leaves them waiting.
    requests = [] if args.day else take(app.layout, "seal", app.keys_dir)
    started = utc_now()
    try:
        sealer = _sealer(app)
    except SealConfigError as exc:
        # Every 5 minutes the same error: the alert is raised once a day per cause, the log
        # line every time (logs\core.log, Task Scheduler result 0x1).
        log.error("command seal failed: %s", exc)
        try:
            config_alert(app.layout, exc.cause, exc.error)
        except OSError as write_error:
            log.error("could not write alert R20-seal-config: %s", write_error)
        code = "executable_refused" if exc.cause == "executable" else "seal_failed"
        for request in requests:
            finish(app.layout, request, task_result(request, started_at=started, code=code))
        print(f"Hata: {exc}", file=sys.stderr)
        return 1
    if args.day:
        outcomes = [sealer.seal_day(args.day)]
    else:
        wanted = {request.params.day: request.id for request in requests if request.params.day}
        outcomes = sealer.seal_due(requested=wanted)
        for request in requests:
            finish(app.layout, request, _seal_result(sealer, request, outcomes, started))
        clean(app.layout)
    if not outcomes:
        print("Mühürlenecek kapanmış gün yok.")
        return 0
    for outcome in outcomes:
        print(f"{outcome.day.isoformat()}  {outcome.status:8}  {outcome.message}")
    return 0 if all(o.status == "sealed" for o in outcomes) else 1


def _seal_result(
    sealer: Sealer, request: TaskRequest, outcomes: Sequence[SealOutcome], started: datetime
) -> TaskResult:
    """What this run did with the requested day; when it did not reach the day, why: the day
    was sealed (or skipped, deleted) before, or an earlier day still waits."""
    day = request.params.day
    if day is None:  # pragma: no cover - a seal request always names its day (the model)
        raise ValueError(f"seal request {request.id} names no day")
    state = sealer.state(day)
    outcome = next((o for o in outcomes if o.day == day), None)
    if outcome is None:
        status = str(state.get("status"))
        outcome = (
            SealOutcome(day, status, f"{day} is {status}")
            if status in (SEALED, SKIPPED, DELETED)
            else SealOutcome(day, "waiting", "an earlier day is not sealed yet")
        )
    sealed = outcome.status == SEALED
    result = SealResult.model_validate(
        {
            "day": day,
            "status": outcome.status,
            "message": outcome.message,
            "serial": state.get("serial") if sealed else None,
            "gen_time": state.get("gen_time") if sealed else None,
        }
    )
    if not sealed:
        return task_result(request, started_at=started, code="seal_failed", result=result)
    texts = (
        f"{day.strftime('%d.%m.%Y')} günü mühürlendi ve doğrulandı.",
        f"{day.isoformat()} was sealed and verified.",
    )
    return task_result(request, started_at=started, result=result, texts=texts)


def cmd_verify(args: argparse.Namespace, app: App) -> int:
    available = sealed_days(app.layout)
    if args.day:
        days = [args.day]
    elif args.all:
        days = available
    else:
        days = available[-args.last :] if args.last else available[-1:]
    if not days:
        print("Doğrulanacak mühürlü gün yok.")
        return 0
    checks = verify_range(
        app.layout,
        days,
        app.roots(),
        deep=args.deep,
        legal_roots=app.config.tsa.legal_root_fingerprints,
    )
    ok = all(check.ok for check in checks)
    summary = {
        "at": isoformat_site(utc_now(), "seconds"),
        "from": days[0].isoformat(),
        "to": days[-1].isoformat(),
        "days": len(checks),
        "deep": args.deep,
        "ok": ok,
        "failed_days": [c.day.isoformat() for c in checks if not c.ok],
    }
    kind = "full" if args.all else "day" if args.day else f"last-{len(days)}"
    atomic_write_text(
        app.layout.state / "verify" / f"last-{kind}.json",
        json.dumps(
            {**summary, "checks": [c.as_json() for c in checks]}, ensure_ascii=False, indent=2
        )
        + "\n",
    )
    if args.all:  # the monthly full-chain result is sealed into the next manifest
        atomic_write_text(
            app.layout.state / "verify" / "pending-chain-check.json",
            json.dumps(summary, ensure_ascii=False) + "\n",
        )
    if not ok:
        raise_alert(
            app.layout.alerts,
            "R20-verify",
            "critical",
            f"Zincir doğrulaması başarısız: {', '.join(summary['failed_days'])}",
            summary,
        )
    if args.json:
        _print_json([c.as_json() for c in checks])
    else:
        for check in checks:
            state = "OK " if check.ok else "HATA"
            print(f"{check.day.isoformat()}  {state}  {'; '.join(check.errors or check.warnings)}")
    return 0 if ok else 1


def cmd_replica(args: argparse.Namespace, app: App) -> int:
    target = app.config.replica.target
    if not app.config.replica.enabled or target is None:
        print("Replika kapalı (replica.enabled = false).")
        return 0
    user = app.config.replica.user
    try:
        password = app.secrets.get(REPLICA_PASSWORD) if user else None
    except SecretStoreError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2
    try:
        with connected(target, user, password):
            if args.test:
                print(f"Replika bağlantısı çalışıyor: {probe(target)}")
                return 0
            outcomes = (
                [replicate_day(app.layout, args.day, target)]
                if args.day
                else replicate_due(app.layout, target)
            )
    except OSError as exc:  # ShareError is an OSError; never contains the password
        raise_alert(
            app.layout.alerts,
            "R20-replica",
            "high",
            f"Replika hedefine bağlanılamadı: {exc}",
            {"target": str(target), "user": user or ""},
        )
        print(f"Replika hedefine bağlanılamadı: {exc}", file=sys.stderr)
        return 1
    for outcome in outcomes:
        print(
            f"{outcome.day.isoformat()}  {outcome.status:10}  {outcome.copied} dosya  "
            f"{outcome.message}"
        )
    return 0 if all(o.status != "failed" for o in outcomes) else 1


def cmd_retention(args: argparse.Namespace, app: App) -> int:
    result = run_retention(
        app.config, provider=app.provider(), roots=app.roots(), dry_run=args.dry_run
    )
    if not result.enabled:
        print(f"Saklama/imha kapalı. Süresi dolan gün sayısı: {len(result.candidates)}")
        return 0
    if args.dry_run:
        print(
            f"Silinecek günler (< {result.cutoff}): "
            + (", ".join(d.isoformat() for d in result.candidates) or "yok")
        )
        return 0
    if result.error:
        print(f"HATA: {result.error}")
        return 1
    print(f"Silinen gün: {len(result.deleted)}; imha tutanağı: {result.record or '-'}")
    return 0


def cmd_credit(_: argparse.Namespace, app: App) -> int:
    credits = check_credit(app.provider(), app.layout, app.config.tsa.credit_alert_threshold)
    if credits is None:
        print("Kontör bilgisi alınamadı ya da sağlayıcıda kontör yok.")
        return 1 if app.config.tsa.provider == "zamane" else 0
    years = credits / 365
    print(f"Kalan kontör: {credits} (~{years:.1f} yıl, günde 1 damga)")
    return 0 if credits >= app.config.tsa.credit_alert_threshold else 1


def cmd_clock(_: argparse.Namespace, app: App) -> int:
    sample = check_clock(app.config)
    if sample.get("warning"):
        log.warning("clock-check: %s", sample["warning"])
    _print_json(sample)
    offset = sample.get("offset_us")
    within = isinstance(offset, int) and abs(offset) <= app.config.clock.alert_offset_ms * 1000
    return 0 if sample.get("ok") and within else 1


@dataclass(frozen=True)
class TsaTest:
    """One stamp test: ``report`` is the JSON of ``arcilogger-core tsa-test``, None when no
    stamp was obtained (``error`` says why)."""

    report: dict[str, Any] | None
    error: str | None = None

    @property
    def verified(self) -> bool:
        return self.report is not None and self.report["verified"] is True


def run_tsa_test(app: App) -> TsaTest:
    """Stamp a random 1 KB file and verify the token independently (uses 1 credit). Shared by
    ``tsa-test`` and the browser's "Damga testi" (Requests task). A configuration that cannot
    stamp at all (no provider, no pinned roots) raises, as before."""
    folder = app.layout.state / "tsa-test"
    folder.mkdir(parents=True, exist_ok=True)
    stamp_name = utc_now().strftime("%Y%m%dT%H%M%SZ")
    sample = folder / f"tsa-test-{stamp_name}.bin"
    sample.write_bytes(pysecrets.token_bytes(1024))
    provider = app.provider()
    try:
        stamp = provider.stamp(sample)
    except TsaError as exc:
        return TsaTest(None, str(exc))
    result = verify_token(
        stamp.token_path.read_bytes(),
        roots=app.roots(),
        file_path=sample,
        require_critical_eku=app.config.tsa.require_critical_eku,
    )
    info = result.info
    return TsaTest(
        {
            "provider": provider.name,
            # The configuration points at the Kamu SM production server …
            "legal_grade_claimed": provider.legal_grade,
            # … and the verified chain ends at a root listed in tsa.legal_root_fingerprints.
            "legal_grade": confirmed_legal_grade(
                provider.legal_grade, result, app.config.tsa.legal_root_fingerprints
            ),
            # Compare with Kamu SM's published value before listing it as a legal root.
            "root_sha256": result.details.get("root_sha256") or None,
            "root": result.details.get("root") or None,
            "verified": result.ok,
            "errors": list(result.errors),
            "warnings": list(result.warnings),
            "gen_time": info.gen_time.isoformat() if info else None,
            "serial": info.serial if info else None,
            "tsa": info.tsa_name if info else None,
            "tsa_cert_not_after": info.tsa_cert_not_after if info else None,
            "container": info.container if info else None,
            "token": str(stamp.token_path),
        }
    )


def cmd_tsa_test(_: argparse.Namespace, app: App) -> int:
    """Connection test: stamp a random 1 KB file and verify it (uses 1 credit)."""
    test = run_tsa_test(app)
    if test.report is None:
        print(f"Zaman damgası alınamadı: {test.error}")
        return 1
    _print_json(test.report)
    return 0 if test.verified else 1


# -- the Requests task (ADR-0014; contract 2.9) ---------------------------------------------------


@dataclass(frozen=True)
class TaskDone:
    """What a task request did: ``code`` is None when it succeeded, ``texts`` then say what
    (Turkish, English); ``result`` holds the facts (never a secret or a file path)."""

    code: str | None
    result: Any = None
    texts: tuple[str, str] | None = None


#: The code of a request of each kind that failed for a reason of its own.
FAILURE_CODES: dict[TaskKind, str] = {
    "seal": "seal_failed",
    "credit": "credit_query_failed",
    "tsa_test": "tsa_test_failed",
    "replica_test": "replica_test_failed",
}


def _not_started(exc: Exception, kind: TaskKind) -> TaskDone:
    """A request whose provider could not be built: refused by the executable policy, or the
    kind's own failure (credentials, secret store, configuration; the log has the detail)."""
    log.error("%s request: %s", kind, exc)
    refused = isinstance(exc, TsaExecutableError)
    return TaskDone("executable_refused" if refused else FAILURE_CODES[kind])


def run_tsa_test_request(app: App) -> TaskDone:
    """The stamp test ("Damga testi", 1 credit): ``tsa-test``, its JSON without the token's
    path as the result."""
    try:
        test = run_tsa_test(app)
    except (TsaError, SecretStoreError) as exc:
        return _not_started(exc, "tsa_test")
    if test.report is None:
        log.warning("tsa_test request: no time stamp: %s", test.error)
        return TaskDone("tsa_test_failed")
    facts = TsaTestResult.model_validate(
        {key: value for key, value in test.report.items() if key != "token"}
    )
    if not test.verified:
        return TaskDone("tsa_test_failed", facts)
    return TaskDone(
        None,
        facts,
        (
            "Zaman damgası alındı ve bağımsız olarak doğrulandı.",
            "A time stamp was obtained and verified independently.",
        ),
    )


def run_credit_request(app: App) -> TaskDone:
    """The credit query ("Kontör sorgula"): the daily credit check, now (``credit.jsonl``,
    ``R20-credit``)."""
    threshold = app.config.tsa.credit_alert_threshold
    try:
        provider = app.provider()
    except (TsaError, SecretStoreError) as exc:
        return _not_started(exc, "credit")
    credits = check_credit(provider, app.layout, threshold)
    if credits is None:
        return TaskDone("credit_query_failed")
    return TaskDone(
        None,
        CreditResult(credits=credits, threshold=threshold, below_threshold=credits < threshold),
        (
            f"Kalan kontör: {credits} (uyarı eşiği {threshold}).",
            f"Credits left: {credits} (alert threshold {threshold}).",
        ),
    )


def _replica_problem(exc: Exception, target: Path) -> str:
    """What went wrong, without a path: a result never names one (contract 2.4.6)."""
    if isinstance(exc, SecretStoreError):
        return "the stored replica.password cannot be read (secret store)"
    if isinstance(exc, OSError) and exc.strerror:  # the file name is not part of it
        return exc.strerror
    text = str(exc)
    for path in {str(target), target.as_posix(), unc_root(target) or ""} - {""}:
        text = text.replace(path, "<replica>")
    return text


def run_replica_test_request(app: App) -> TaskDone:
    """The replica test ("Replika testi"), as ``replica --test``: connect to the share with
    ``replica.user`` and its stored password, write, read back and delete a probe file. The
    saved target is tested whether the replica is enabled yet or not."""
    target = app.config.replica.target
    if target is None:
        return TaskDone(
            "replica_test_failed", ReplicaTestResult(ok=False, message="replica.target is not set")
        )
    user = app.config.replica.user
    try:
        password = app.secrets.get(REPLICA_PASSWORD) if user else None
        with connected(target, user, password):
            probe(target)
    except (OSError, SecretStoreError) as exc:  # ShareError is an OSError, never the password
        log.warning("replica_test request: %s", exc)
        problem = _replica_problem(exc, target)
        return TaskDone("replica_test_failed", ReplicaTestResult(ok=False, message=problem))
    return TaskDone(
        None,
        ReplicaTestResult(ok=True, message="write, read back and delete work"),
        (
            "Replika paylaşımına bağlanıldı; deneme dosyası yazıldı, okundu ve silindi.",
            "The replica share was reached; a probe file was written, read back and deleted.",
        ),
    )


def cmd_requests(_: argparse.Namespace, app: App) -> int:
    """``requests run``: every valid request of ``state\\requests\\tasks``, oldest first (the
    task ``\\ARCILOGGER\\Requests``, woken by Event 4111 and every 5 minutes). Exit 0 when there
    is none or all succeeded, 1 when one failed (its result says why)."""
    runners = {
        "credit": run_credit_request,
        "tsa_test": run_tsa_test_request,
        "replica_test": run_replica_test_request,
    }
    failed = 0
    for request in take(app.layout, "tasks", app.keys_dir):
        started = utc_now()
        runner = runners.get(request.kind)
        try:
            done = TaskDone(FAILURE_CODES[request.kind]) if runner is None else runner(app)
        except Exception:  # one broken request must not keep the next one from running
            log.exception("task request %s (%s) failed", request.id, request.kind)
            done = TaskDone(FAILURE_CODES[request.kind])
        result = task_result(
            request, started_at=started, code=done.code, result=done.result, texts=done.texts
        )
        finish(app.layout, request, result)
        log.info("task request %s (%s): %s", request.id, request.kind, done.code or "ok")
        print(f"{request.id}  {request.kind:12}  {done.code or 'ok'}")
        failed += done.code is not None
    clean(app.layout)
    return 1 if failed else 0


def cmd_secret(args: argparse.Namespace, app: App) -> int:
    store = app.secrets
    action = args.secret_command
    if action == "init":
        key = app.config.secrets_dir / "secret.key"
        if key.exists():
            print(f"Anahtar zaten var: {key}")
            return 0
        SecretStore.create_key(key)
        print(f"Anahtar oluşturuldu: {key} (dosya izinlerini kısıtlayın)")
        return 0
    if action == "list":
        for name in store.names():
            print(f"{name}  (kayıtlı ✓)")
        return 0
    if args.name not in SECRET_NAMES:
        raise UsageError(f"unknown secret {args.name!r}; allowed: {', '.join(SECRET_NAMES)}")
    if action == "delete":
        deleted = store.delete(args.name)
        print("silindi" if deleted else "kayıtlı değildi")
        return _record_secret_change(app, args.name, "delete", "deleted" if deleted else "absent")
    value = getpass.getpass(f"{args.name}: ") if sys.stdin.isatty() else sys.stdin.readline()
    # A pipe from Windows PowerShell 5.1 may start with a UTF-8 BOM; it is never part of a secret.
    store.set(args.name, value.removeprefix("\ufeff").rstrip("\r\n"))
    print(f"{args.name} kaydedildi.")
    return _record_secret_change(app, args.name, "set", "stored")


def _record_secret_change(app: App, name: str, action: str, result: str) -> int:
    """Credential changes are audited (spec 6.10, 8.5): the worker copies this line into the
    audit log. A change that cannot be recorded is reported, never silently accepted."""
    try:
        record_change(
            app.layout.secret_changes,
            name=name,
            action=action,
            result=result,
            operator=_operator(),
        )
    except OSError as exc:
        print(
            f"UYARI: değişiklik denetim kaydı için yazılamadı ({app.layout.secret_changes}: "
            f"{exc.strerror or exc}). Değişiklik yapıldı; bunu yöneticiye bildirin.",
            file=sys.stderr,
        )
        return 1
    log.info("secret %s: %s (%s) by %s", name, action, result, _operator())
    return 0


def cmd_status(_: argparse.Namespace, app: App) -> int:
    layout = app.layout
    days = sealed_days(layout)
    today = site_day(utc_now())
    pending = [
        d.isoformat()
        for d in Sealer(app.config, provider=_NoProvider(), roots=[]).closed_unsealed_days()
    ]

    def last_line(path: Path) -> Any:
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        return json.loads(lines[-1]) if lines else None

    status = {
        "version": __version__,
        "tsa_provider": app.config.tsa.provider,
        "legal_grade": app.config.tsa.legal_grade,
        "last_sealed_day": days[-1].isoformat() if days else None,
        "yesterday_sealed": (today - timedelta(days=1)) in days,
        "pending_days": pending,
        "credit": last_line(layout.credit_log),
        "clock": last_line(layout.clock_samples(today)),
        "retention_enabled": app.config.retention.enabled,
        "replica_enabled": app.config.replica.enabled,
    }
    _print_json(status)
    return 0 if not pending or pending == [(today - timedelta(days=1)).isoformat()] else 1


def cmd_config_check(args: argparse.Namespace, app: App) -> int:
    """The pre-flight check of a production configuration (:func:`setup_problems`): exit code 1
    while a required value is missing, warnings alone keep 0."""
    problems = setup_problems(app.config)
    failed = any(problem.level == "error" for problem in problems)
    if args.json:
        _print_json({"ok": not failed, "problems": [asdict(problem) for problem in problems]})
    else:
        for problem in problems:
            label = "HATA [ERROR]" if problem.level == "error" else "UYARI [WARNING]"
            print(f"{label}  {problem.key}: {problem.message}")
        if not problems:
            print("Yapılandırma tamam [the configuration is complete].")
    return 1 if failed else 0


def _operator() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except (KeyError, OSError):  # no user name in the environment (service account)
        return "cli"


def cmd_evidence(args: argparse.Namespace, app: App) -> int:
    if args.evidence_command == "purge":
        deleted = purge_expired(app.layout, args.keep_days)
        for path in deleted:
            print(f"silindi: {path.name}")
        print(f"{len(deleted)} eski kanıt paketi silindi ({args.keep_days} günden eski).")
        return 0
    try:
        refs = load_refs(args.extract) if args.extract else []
        report = args.pdf.read_bytes() if args.pdf else None
        info = build_package(
            app.layout,
            package_days(args.first, args.last),
            roots=app.roots(),
            sources=args.source,
            extract=refs,
            out_dir=args.out,
            encrypt=args.password,
            report_pdf=report,
            report_name=args.pdf.name if args.pdf else "rapor.pdf",
            created_by=args.creator or _operator(),
            lookup_id=args.lookup_id,
            reference=args.reference,
            site_name=app.config.site_name,
            crl_dir=args.crl_dir or app.layout.state / "crl",
            include_audit_log=args.include_audit_log,
            legal_root_fingerprints=app.config.tsa.legal_root_fingerprints,
        )
    except (EvidenceError, OSError) as exc:
        print(f"Kanıt paketi oluşturulamadı: {exc}", file=sys.stderr)
        return 1
    if args.json:
        data = info.as_json()
        if info.password:
            data["password"] = info.password
        _print_json(data)
        return 0
    print(f"Kanıt paketi oluşturuldu: {info.path}")
    print(f"  Paket          : {info.package_id}")
    print(f"  SHA-256        : {info.sha256}")
    print(f"  Boyut          : {format_bytes(info.size)} ({info.file_count} dosya)")
    print(f"  Günler         : {', '.join(day.isoformat() for day in info.days)}")
    print(f"  paket.json     : SHA-256 {info.index_sha256}")
    print(f"  verify.py      : SHA-256 {info.verifier_sha256} (alıcıya paketten ayrı iletin)")
    if info.legal_grade_claimed and not info.legal_grade:
        print(
            "  NOT            : günler Kamu SM üretim damgası beyan ediyor ve damgalar doğrulandı; "
            "kök sertifikanın parmak izi tsa.legal_root_fingerprints listesinde olmadığı için "
            "yasal geçerli olarak gösterilmiyor (kökün parmak izini Kamu SM'nin yayımladığı "
            "değerle karşılaştırın)"
        )
    elif not info.legal_grade:
        print("  UYARI          : yasal geçerliliği olmayan test damgalı gün(ler) var")
    if info.password:
        print(f"  Parola         : {info.password}")
        print("  Parola yalnızca şimdi gösteriliyor ve hiçbir yerde saklanmıyor.")
    return 0


def cmd_evidence_verify(args: argparse.Namespace) -> int:
    """Run ARCILOGGER's own copy of verify.py (never the package's) in an isolated Python."""
    target: Path = args.path
    extra = [flag for flag, on in (("--hizli", args.quick), ("--json", args.json)) if on]
    if args.roots is not None:
        extra += ["--kokler", str(args.roots)]
    try:
        encrypted = target.is_file() and zipfile.is_zipfile(target) and is_encrypted(target)
    except (OSError, zipfile.BadZipFile):
        encrypted = False
    if not encrypted:
        return run_verifier(target, *extra, capture=False).returncode
    if sys.stdin.isatty():
        password = getpass.getpass("Paket parolası: ")
    else:
        password = sys.stdin.readline().rstrip("\r\n")
    with tempfile.TemporaryDirectory(prefix="arcilogger-kanit-") as folder:
        try:
            extract_archive(target, Path(folder), password)
        except EvidenceError as exc:
            print(f"Paket açılamadı: {exc}", file=sys.stderr)
            return 2
        return run_verifier(Path(folder), *extra, capture=False).returncode


class _NoProvider:
    name = "none"
    legal_grade = False

    def stamp(self, file_path: Path) -> Any:  # pragma: no cover - never called by status
        raise TsaError("status does not stamp")

    def credits(self) -> int | None:  # pragma: no cover
        return None


# -- parser ---------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arcilogger-core",
        description=f"{PRODUCT_NAME} core — sealing, verification and retention tools.",
    )
    parser.add_argument("--version", action="version", version=f"{PRODUCT_NAME} {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to arcilogger.toml")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser("version", help="print the version")
    commands.add_parser("info", help="print version, platform and current site time")

    seal = commands.add_parser("seal", help="seal closed days (default: all that are due)")
    seal.add_argument("--day", type=_day)

    verify = commands.add_parser("verify", help="verify sealed days and the chain")
    group = verify.add_mutually_exclusive_group()
    group.add_argument("--day", type=_day)
    group.add_argument("--last", type=int, metavar="N")
    group.add_argument("--all", action="store_true", help="the whole chain")
    verify.add_argument("--deep", action="store_true", help="also decompress and hash raw data")
    verify.add_argument("--json", action="store_true")

    replica = commands.add_parser("replica", help="copy sealed days to the second location")
    replica.add_argument("--day", type=_day)
    replica.add_argument(
        "--test", action="store_true", help="connect, write, read and delete a probe file only"
    )

    retention = commands.add_parser("retention", help="delete days past the retention period")
    retention.add_argument("--dry-run", action="store_true")

    commands.add_parser("credit", help="query the Kamu SM credit")
    commands.add_parser("clock-check", help="measure the clock offset (SNTP)")
    commands.add_parser("tsa-test", help="stamp and verify a random file (uses 1 credit)")
    requests = commands.add_parser(
        "requests", help="signed requests of the browser administration (Requests task)"
    )
    request_commands = requests.add_subparsers(dest="requests_command", required=True)
    request_commands.add_parser(
        "run", help="run the waiting credit query, TSA test and replica test requests"
    )
    commands.add_parser("status", help="summary for monitoring")
    config_check = commands.add_parser(
        "config-check", help="what a production configuration still lacks (exit 1: incomplete)"
    )
    config_check.add_argument("--json", action="store_true")

    secret = commands.add_parser("secret", help="encrypted credentials")
    secret_commands = secret.add_subparsers(dest="secret_command", required=True)
    secret_commands.add_parser("init", help="create the encryption key")
    secret_commands.add_parser("list", help="names of stored secrets")
    set_cmd = secret_commands.add_parser("set", help="store a secret (read from the console)")
    set_cmd.add_argument("name")
    delete_cmd = secret_commands.add_parser("delete", help="remove a secret")
    delete_cmd.add_argument("name")

    evidence = commands.add_parser("evidence", help="evidence packages (Kanıt Paketi)")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    build = evidence_commands.add_parser("build", help="build an evidence package (ZIP)")
    build.add_argument("--from", dest="first", type=_day, required=True, metavar="YYYY-MM-DD")
    build.add_argument("--to", dest="last", type=_day, required=True, metavar="YYYY-MM-DD")
    build.add_argument(
        "--source", action="append", metavar="NAME", help="only these sources (repeatable)"
    )
    build.add_argument(
        "--extract",
        type=Path,
        metavar="FILE.json",
        help='raw lines to extract: [{"day": "YYYY-MM-DD", "source": "…", "line": N}, …]',
    )
    build.add_argument(
        "--password",
        action="store_true",
        help="AES-256 encrypted ZIP; a random password is printed once and not stored",
    )
    build.add_argument("--out", type=Path, metavar="DIR", help="output folder (default exports/)")
    build.add_argument("--pdf", type=Path, metavar="FILE", help="report to include (PDF)")
    build.add_argument("--creator", help="who builds the package (default: the OS user)")
    build.add_argument("--lookup-id", help="5651 lookup the package belongs to")
    build.add_argument("--reference", help="request reference, e.g. the authority's file number")
    build.add_argument("--crl-dir", type=Path, metavar="DIR", help="CRL folder (default state/crl)")
    build.add_argument(
        "--include-audit-log",
        action="store_true",
        help="also include each day's ARCILOGGER audit log (left out by default: it holds other "
        "requests and staff data; for internal audits only)",
    )
    build.add_argument("--json", action="store_true")
    verify_package = evidence_commands.add_parser(
        "verify", help="verify a package with the standalone verifier (needs no configuration)"
    )
    verify_package.add_argument("path", type=Path, help="package folder or ZIP file")
    verify_package.add_argument("--quick", action="store_true", help="skip decompression")
    verify_package.add_argument("--roots", type=Path, metavar="DIR", help="trust only these roots")
    verify_package.add_argument("--json", action="store_true")
    purge = evidence_commands.add_parser("purge", help="delete old packages from exports/")
    purge.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS, metavar="N")
    return parser


HANDLERS = {
    "seal": cmd_seal,
    "verify": cmd_verify,
    "replica": cmd_replica,
    "retention": cmd_retention,
    "credit": cmd_credit,
    "clock-check": cmd_clock,
    "tsa-test": cmd_tsa_test,
    "requests": cmd_requests,
    "secret": cmd_secret,
    "status": cmd_status,
    "config-check": cmd_config_check,
    "evidence": cmd_evidence,
}


def _setup_logging(app: App) -> None:
    logs = app.layout.logs
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(logs / "core.log", encoding="utf-8")],
        force=True,
    )


def _utf8_console() -> None:
    """Turkish text (ı, ş, ğ) must not crash on a cp1252 Windows console or pipe."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    _utf8_console()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"{PRODUCT_NAME} {__version__}")
        return 0
    if args.command == "info":
        return cmd_info(args)
    if args.command == "evidence" and args.evidence_command == "verify":
        return cmd_evidence_verify(args)  # an auditor's machine has no arcilogger.toml
    handler = HANDLERS.get(args.command or "")
    if handler is None:
        parser.print_help()
        return 2
    try:
        app = App(args.config)
    except ConfigLocationError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        if args.command == "seal":  # sealing stops: R20-seal-config, once a day
            try:
                config_alert(Layout(exc.data_root), "location", exc)
            except OSError as write_error:
                print(f"R20-seal-config yazılamadı: {write_error}", file=sys.stderr)
        return 2
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Yapılandırma okunamadı: {exc}", file=sys.stderr)
        return 2
    _setup_logging(app)
    try:
        return handler(args, app)
    except UsageError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2
    except TsaError as exc:
        log.error("command %s failed: %s", args.command, exc)
        print(f"Hata: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
