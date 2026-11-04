"""The legal-validity samples (``web/e2e/samples/legal-status.json``), captured from the real API.

``capture_api_samples.py`` calls :func:`capture` on its main site after every other page's
samples (Wave 5 contract 2.3.1; the status reads the whole site as the pages before it left it),
with the capture (the app, its data directory, the request helpers ``json`` and ``error``) and
three signed-in clients: an administrator, an auditor and an administrator who has not accepted
the KVKK notice. It returns ``{sample file name: data}``.

``GET /legal-status`` is computed from the configuration and the state files only
(:func:`arcilogger_api.legalstatus.compute.compute_legal_status`), so each case is a site at the
capture's "now" (13.10.2026 07:00) with a ``[tsa]`` configuration and the state files of a
running site:

* the site: the capture's own (04.-10.10 sealed by the mock TSA, 11.10 failed, 12.10 waiting)
  with its own configuration (``mock``), or for the Kamu SM production server (``zamane``) a
  provider site of its own (:func:`provider_site`): the same days in a data directory of their
  own, the sealed ones stamped through a made-up CA outside ARCILOGGER's test organization
  (:func:`arcilogger_api.legalstatus.testing.simulated_ca`), as a real provider's stamps are.
  Its stamps need that CA's lists, while ARCILOGGER's test root needs none (W5-C-13, W5-E-5);
* the Kamu SM configuration: the CA's root pinned in ``tsa.roots_dir`` and its fingerprint in
  ``tsa.legal_root_fingerprints``, or without that fingerprint and with
  ``seal.late_after_hours`` 24, so the unsealed 11.10 (31 hours after its end) is late;
* ``state/renewal/renewal.json`` as §2.1.2 describes it (on a site the ``Renew`` task writes it
  with ``arcilogger-core renew``, W5-A-2; here only ``tokens`` and ``totals``): the sealed days'
  tokens, ``not_due`` with the TSA certificate's real expiry, or ``due`` for a provider
  certificate that ends on 01.03.2027 (within ``tsa.renew_before_days``);
* stored revocation lists of the stamps' root as ``crl fetch`` stores them
  (:mod:`arcilogger_api.legalstatus.testing`): one issued yesterday and fetched an hour ago, a
  root's list issued 90 days ago and valid for months whose last fetch is nine days old (older
  than ``tsa.crl_max_age_days``, C22), or one issued yesterday that revokes the TSA certificate
  the day before the stamps.

What ``legal-status.json`` holds (the names of the W5-D fixtures, ``web/src/test/w5-fixtures.ts``):
``ok`` (Kamu SM, nothing to report), ``provider_not_legal`` (the site as it is, a fresh list:
the one danger reason), ``warning_only`` (``renewal_due``, ``crl_stale``), ``mixed``
(``legal_root_missing``, ``unstamped_days``, ``renewal_due``, ``crl_stale``), ``revoked``
(``certificate_revoked``), each the auditor's answer (the administrator and the administrator who
has not accepted the KVKK notice get the same: the banner is every signed-in user's, D11), and
``errors``: ``not_authenticated`` (no session). ``credentials_in_clear`` (TÜRKTRUST over plain
HTTP) and ``renewal_uncovered`` (tokens older than their certificate) are not captured: the
configuration knows no ``turktrust`` before W5-T, and the capture's vault has no token of an
earlier certificate.

The 60-second answer cache is cleared before every case. Everything the module writes is put back
afterwards: the configuration and the data directory the app reads, ``state/renewal/renewal.json``,
``state/crl`` and the pinned root folder; the provider site is removed.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import samples_system
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from fastapi.testclient import TestClient

from arcilogger_api import deps
from arcilogger_api.legalstatus.testing import CA_POLICY, root_list, simulated_ca, store_lists
from arcilogger_core.config import KAMUSM_PRODUCTION_HOST, Config
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import utc_now
from arcilogger_core.tsa.mock import MockTsa
from arcilogger_core.tsa.verify import fingerprint
from arcilogger_worker.alerts.system import (
    NoStamps,
    pinned_roots,
    sealed_days,
    stamp_in_use,
    stamp_renewal_path,
)
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

if TYPE_CHECKING:
    from capture_api_samples import Capture

#: Keys this module's file compares differently, added to the script's defaults:
#: ``{file: (data-map keys, enum keys)}`` (``capture_api_samples.MAP_KEYS``, ``ENUM_KEYS``).
#: The banner maps the level, the provider and a reason's link to colours, labels and pages.
SHAPE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "legal-status": (frozenset(), frozenset({"level", "link", "provider"})),
}

API = "/api/v1/legal-status"

#: The provider certificate's end in a report with tokens due for re-stamping.
DUE_NOT_AFTER = datetime(2027, 3, 1, 9, 0, tzinfo=UTC)
#: The fresh list is issued this long ago; the stale one was last fetched this long ago, longer
#: than ``tsa.crl_max_age_days`` (7, C22).
FRESH = timedelta(days=1)
STALE = timedelta(days=9)
#: The stale one is a root's list, valid for months (C22): only its last fetch makes it stale.
ROOT_LIST_DAYS = 120


@dataclass(frozen=True)
class Case:
    """One answer of ``GET /legal-status``: what the site holds, and the reasons it must give."""

    name: str
    #: ``mock`` (the capture's site and configuration), ``legal`` or ``no_root`` (Kamu SM
    #: production, on the provider site).
    tsa: str
    #: The status of every sealed day's token in ``state/renewal/renewal.json``.
    renewal: str
    #: The stored list: ``fresh``, ``stale`` or ``revoked``.
    crl: str
    reasons: tuple[str, ...]
    seal: dict[str, Any] = field(default_factory=dict)


CASES = (
    Case("ok", "legal", "not_due", "fresh", ()),
    Case("provider_not_legal", "mock", "not_due", "fresh", ("provider_not_legal",)),
    Case("warning_only", "legal", "due", "stale", ("crl_stale", "renewal_due")),
    Case(
        "mixed",
        "no_root",
        "due",
        "stale",
        ("legal_root_missing", "unstamped_days", "crl_stale", "renewal_due"),
        {"late_after_hours": 24},
    ),
    Case("revoked", "legal", "not_due", "revoked", ("certificate_revoked",)),
)


@contextmanager
def kept_folder(path: Path, spare: Path) -> Iterator[None]:
    """The folder ``path`` as it is now (or its absence), put back afterwards; ``spare`` holds
    the copy meanwhile."""
    had = path.exists()
    if had:
        shutil.copytree(path, spare)
    try:
        yield
    finally:
        shutil.rmtree(path, ignore_errors=True)
        if had:
            shutil.move(spare, path)


@dataclass(frozen=True)
class Site:
    """A data directory a case reads, the mock TSA that stamped it and the lists of its root
    (``{crl: (list, fetched_at)}``)."""

    layout: Layout
    tsa: MockTsa
    lists: dict[str, tuple[bytes, datetime]]


def provider_site(capture: Capture, folder: Path) -> SealedVault:
    """A data directory of its own (``folder/data``) whose stamps chain to a made-up CA, as a
    real provider's do: the capture's sealed days written and sealed again, each right after
    its end, through a mock TSA with that CA's root and certificates and a policy outside the
    test arc, and the capture's closed unsealed days written only. ARCILOGGER's test root needs
    no list (W5-C-13), so only this site's stamps can give ``crl_stale``."""
    # a failed attempt leaves a manifest without a token: never sealed
    days = [day for day in sealed_days(capture.layout) if capture.layout.token(day).is_file()]
    pending = Sealer(
        capture.config, provider=NoStamps(), roots=[], clock=utc_now
    ).closed_unsealed_days()
    if not days or not pending:
        raise SystemExit("legal-status: the capture's site has no sealed or no unsealed day")
    data_dir = folder / "data"
    simulated_ca(Layout(data_dir).state / "mock-tsa")
    vault = make_vault(data_dir, root=folder, tsa_now=at_site(days[0]))
    vault.config = vault.config.model_copy(
        update={"seal": vault.config.seal.model_copy(update={"first_day": days[0]})}
    )
    vault.tsa.policy = CA_POLICY
    for day in days:
        vault.write_day(day)
        outcome = vault.seal(day)
        if outcome.status != "sealed":
            vault.unlock_all()
            raise SystemExit(f"legal-status: sealing {day} failed: {outcome.message}")
    for day in pending:
        vault.write_day(day)
    return vault


def removed(vault: SealedVault) -> None:
    """The provider site's folder, its sealed files unlocked first."""
    vault.unlock_all()
    shutil.rmtree(vault.root, ignore_errors=True)


def site_config(capture: Capture, case: Case, site: Site, roots: Path) -> Config:
    config = capture.config
    if case.tsa != "mock":
        values: dict[str, Any] = {
            "provider": "zamane",
            "url": f"https://{KAMUSM_PRODUCTION_HOST}",
            "roots_dir": roots,
            "legal_root_fingerprints": (
                (fingerprint(site.tsa.root_certificate),) if case.tsa == "legal" else ()
            ),
        }
        config = config.model_copy(
            update={
                "data_dir": site.layout.root,
                "tsa": config.tsa.model_copy(update=values),
            }
        )
    if case.seal:
        config = config.model_copy(update={"seal": config.seal.model_copy(update=case.seal)})
    return config


def renewal_report(layout: Layout, tsa: MockTsa, status: str) -> dict[str, Any]:
    """``state/renewal/renewal.json`` (§2.1.2) for the sealed days' tokens, each of ``status``."""
    not_after = tsa.tsa_certificate.not_valid_after_utc if status == "not_due" else DUE_NOT_AFTER
    tokens = [
        {
            "path": layout.token(day).relative_to(layout.vault).as_posix(),
            "kind": "day",
            "cert_not_after": not_after.isoformat().replace("+00:00", "Z"),
            "status": status,
            "covered_by": None,
        }
        for day in sealed_days(layout)
        if layout.token(day).is_file()
    ]
    if not tokens:
        raise SystemExit("legal-status: the capture's site has no sealed day with a token")
    totals = dict.fromkeys(("not_due", "due", "covered", "late", "uncovered"), 0)
    totals[status] = len(tokens)
    return {"tokens": tokens, "totals": totals}


def capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    """The legal-validity samples (see the module text)."""
    layout = capture.layout
    roots = capture.root / "legal-status-roots"
    renewal = stamp_renewal_path(layout)
    with ExitStack() as stack:
        stack.enter_context(samples_system.kept(renewal))
        stack.enter_context(kept_folder(layout.crl_dir, capture.root / "legal-status-crl"))
        stack.enter_context(kept_folder(roots, capture.root / "legal-status-roots-kept"))
        for dependency in (deps.get_config, deps.get_layout):
            previous = capture.app.dependency_overrides.get(dependency)
            stack.callback(_restore, capture, dependency, previous)
        stack.callback(_clear_cache, capture)
        provider = provider_site(capture, capture.root / "legal-status-provider")
        stack.callback(removed, provider)
        roots.mkdir()
        (roots / "root.pem").write_bytes(provider.tsa.root_certificate.public_bytes(Encoding.PEM))
        own = MockTsa(layout.state / "mock-tsa", key_type="ec")
        sites = {
            "mock": _site(layout, own, pinned_roots(capture.config, layout)),
            "provider": _site(provider.layout, provider.tsa, [provider.tsa.root_certificate]),
        }
        return _capture(capture, admin, auditor, newcomer, sites, roots)


def _site(layout: Layout, tsa: MockTsa, roots: Sequence[x509.Certificate]) -> Site:
    """``layout`` stamped by ``tsa`` (its newest stamp reaching one of ``roots``) and the lists
    of its root the cases store."""
    now = utc_now()
    stamp = stamp_in_use(layout, roots)
    if stamp is None:
        raise SystemExit("legal-status: the newest sealed day's stamp reaches no pinned root")
    lists = {
        "fresh": (root_list(tsa, now - FRESH), now - timedelta(hours=1)),
        # issued 90 days ago and valid for months, last fetched nine days ago (C22)
        "stale": (
            root_list(tsa, now - timedelta(days=90), valid_days=ROOT_LIST_DAYS),
            now - STALE,
        ),
        # revoked the day before the stamps: crl-revoked for every stamp of the site
        "revoked": (
            root_list(tsa, now - FRESH, revoked_at=stamp.gen_time - timedelta(days=1)),
            now - timedelta(hours=1),
        ),
    }
    return Site(layout, tsa, lists)


def _restore(capture: Capture, dependency: Callable[..., Any], previous: Any) -> None:
    if previous is None:
        capture.app.dependency_overrides.pop(dependency, None)
    else:
        capture.app.dependency_overrides[dependency] = previous


def _serving(value: Any) -> Callable[[], Any]:
    """A dependency that answers ``value`` (``deps.get_config``, ``deps.get_layout``)."""
    return lambda: value


def _clear_cache(capture: Capture) -> None:
    """Drop the kept answer (``routes.status_cache``): the next request computes it."""
    capture.app.state.arci_legal_status = None


def _capture(
    capture: Capture,
    admin: Any,
    auditor: Any,
    newcomer: Any,
    sites: dict[str, Site],
    roots: Path,
) -> dict[str, Any]:
    now = utc_now()
    out: dict[str, Any] = {}
    for case in CASES:
        site = sites["mock" if case.tsa == "mock" else "provider"]
        layout = site.layout
        config = site_config(capture, case, site, roots)
        capture.app.dependency_overrides[deps.get_config] = _serving(config)
        capture.app.dependency_overrides[deps.get_layout] = _serving(layout)
        samples_system.write_json(
            stamp_renewal_path(layout), renewal_report(layout, site.tsa, case.renewal), now
        )
        shutil.rmtree(layout.crl_dir, ignore_errors=True)
        der, fetched_at = site.lists[case.crl]
        store_lists(layout, [(site.tsa, der)], fetched_at=fetched_at)
        _clear_cache(capture)
        answer = capture.json(auditor.get(API), 200)
        # D11: the same answer for every signed-in user, before the KVKK notice too
        for other in (admin, newcomer):
            if capture.json(other.get(API), 200) != answer:
                raise SystemExit(f"legal-status {case.name}: the users got different answers")
        codes = tuple(reason["code"] for reason in answer["reasons"])
        if codes != case.reasons:
            raise SystemExit(f"legal-status {case.name}: reasons {codes}, expected {case.reasons}")
        out[case.name] = answer
    if out["ok"]["level"] != "ok" or out["ok"]["provider_label"] != "Kamu SM":
        raise SystemExit(f"legal-status ok: {out['ok']}")
    anonymous = TestClient(capture.app, base_url="http://testserver")
    out["errors"] = {
        "not_authenticated": capture.error(anonymous.get(API), 401, "not_authenticated")
    }
    return {"legal-status": out}
