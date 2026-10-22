"""Mock time-stamp authority for tests, CI and demo mode — **never legally valid**.

A real RFC 3161 TSA in miniature: its own root CA and TSA certificate (EKU id-kp-timeStamping,
critical), CMS SignedData tokens with ESSCertIDv2, nonce echo and a credit counter. Failures can
be injected to test every error path of the sealing pipeline:

``unreachable``, ``timeout``, ``rejection``, ``no_credit``, ``wrong_imprint``, ``expired_cert``,
``bad_signature``.

:class:`MockTsaServer` serves it over HTTP and, like Kamu SM's firewall, drops requests without
``Content-Type: application/timestamp-query``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

from asn1crypto import cms, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from arcilogger_core.fsutil import atomic_write_bytes, atomic_write_text
from arcilogger_core.timeutil import utc_now
from arcilogger_core.tsa.asn1 import TimeStampResp
from arcilogger_core.tsa.base import TsaUnavailableError
from arcilogger_core.tsa.client import Rfc3161Client

#: OID under the "example" arc 2.999 (ITU-T X.660): never confused with a real policy.
MOCK_POLICY = "2.999.5651.1"

KeyType = Literal["rsa", "ec"]
PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey


class MockFailure(StrEnum):
    NONE = "none"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    REJECTION = "rejection"
    NO_CREDIT = "no_credit"
    WRONG_IMPRINT = "wrong_imprint"
    EXPIRED_CERT = "expired_cert"
    BAD_SIGNATURE = "bad_signature"


def _name(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ARCILOGGER TEST - NOT VALID"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def _new_key(key_type: KeyType) -> PrivateKey:
    if key_type == "ec":
        return ec.generate_private_key(ec.SECP256R1())
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _sign_cert(builder: x509.CertificateBuilder, key: PrivateKey) -> x509.Certificate:
    return builder.sign(key, hashes.SHA256())


class MockTsa:
    """In-process TSA. Keys and certificates persist in ``state_dir`` between runs."""

    def __init__(
        self,
        state_dir: Path,
        *,
        key_type: KeyType = "rsa",
        clock: Callable[[], datetime] = utc_now,
        tsa_validity_days: int = 1825,
    ) -> None:
        self.state_dir = state_dir
        self._clock = clock
        self.failure = MockFailure.NONE
        #: Tests only: sign with this certificate and key instead of the TSA's own (e.g. a
        #: certificate without the timeStamping key purpose).
        self.signer: tuple[x509.Certificate, PrivateKey] | None = None
        #: Tests only: RSA keys sign with RSASSA-PSS (SHA-256, MGF1-SHA-256, salt 32).
        self.rsa_pss = False
        #: Tests only: the TSTInfo policy (default: the test arc 2.999, never a real policy).
        self.policy = MOCK_POLICY
        self._lock = threading.Lock()
        state_dir.mkdir(parents=True, exist_ok=True)
        if not (state_dir / "root.pem").exists():
            self._create(key_type, tsa_validity_days)
        self.root_certificate = self._load_cert("root.pem")
        self.tsa_certificate = self._load_cert("tsa.pem")
        self._tsa_key = self._load_key("tsa.key")
        self.expired_certificate = self._load_cert("tsa-expired.pem")
        self._expired_key = self._load_key("tsa-expired.key")

    # -- persistence ----------------------------------------------------------------------------

    def _create(self, key_type: KeyType, tsa_validity_days: int) -> None:
        now = self._clock()
        root_key = _new_key(key_type)
        root_name = _name("ARCILOGGER Mock Root CA (TEST ONLY)")
        root = _sign_cert(
            x509.CertificateBuilder()
            .subject_name(root_name)
            .issuer_name(root_name)
            .public_key(root_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=400))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), False),
            root_key,
        )
        self._write_cert("root.pem", root)
        self._write_key("root.key", root_key)
        for file, start, end in (
            ("tsa", now - timedelta(days=1), now + timedelta(days=tsa_validity_days)),
            ("tsa-expired", now - timedelta(days=400), now - timedelta(days=30)),
        ):
            key = _new_key(key_type)
            cert = _sign_cert(
                x509.CertificateBuilder()
                .subject_name(_name(f"ARCILOGGER Mock TSA (TEST ONLY) {file}"))
                .issuer_name(root_name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(start)
                .not_valid_after(end)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(
                    x509.KeyUsage(
                        digital_signature=True,
                        content_commitment=True,
                        key_encipherment=False,
                        data_encipherment=False,
                        key_agreement=False,
                        key_cert_sign=False,
                        crl_sign=False,
                        encipher_only=False,
                        decipher_only=False,
                    ),
                    critical=True,
                )
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), True)
                .add_extension(
                    x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), False
                ),
                root_key,
            )
            self._write_cert(f"{file}.pem", cert)
            self._write_key(f"{file}.key", key)
        atomic_write_text(self.state_dir / "credits.json", json.dumps({"credits": 10000}))

    def _write_cert(self, name: str, cert: x509.Certificate) -> None:
        atomic_write_bytes(self.state_dir / name, cert.public_bytes(serialization.Encoding.PEM))

    def _write_key(self, name: str, key: PrivateKey) -> None:
        atomic_write_bytes(
            self.state_dir / name,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )

    def _load_cert(self, name: str) -> x509.Certificate:
        return x509.load_pem_x509_certificate((self.state_dir / name).read_bytes())

    def _load_key(self, name: str) -> PrivateKey:
        key = serialization.load_pem_private_key((self.state_dir / name).read_bytes(), None)
        if not isinstance(key, rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey):
            raise TypeError("unsupported mock key")
        return key

    # -- credit ---------------------------------------------------------------------------------

    @property
    def credits(self) -> int:
        data = json.loads((self.state_dir / "credits.json").read_text(encoding="utf-8"))
        return int(data["credits"])

    @credits.setter
    def credits(self, value: int) -> None:
        atomic_write_text(self.state_dir / "credits.json", json.dumps({"credits": value}))

    # -- protocol -------------------------------------------------------------------------------

    def respond(self, request_der: bytes) -> bytes:
        """Answer a DER TimeStampReq with a DER TimeStampResp."""
        with self._lock:
            request = tsp.TimeStampReq.load(request_der)
            if self.failure is MockFailure.REJECTION:
                return _rejection("bad request", "bad_request")
            if self.failure is MockFailure.NO_CREDIT or self.credits <= 0:
                return _rejection("Not enough credit", "system_failure")
            # The API service may only read state\mock-tsa (ADR-0007): the counter is a test aid
            # and the mock is never legal, so a report stamp does not fail over it.
            with contextlib.suppress(PermissionError):
                self.credits = self.credits - 1
            imprint = request["message_imprint"]
            hashed = imprint["hashed_message"].native
            if self.failure is MockFailure.WRONG_IMPRINT:
                hashed = hashlib.new(imprint["hash_algorithm"]["algorithm"].native, hashed).digest()
            cert, key = (
                (self.expired_certificate, self._expired_key)
                if self.failure is MockFailure.EXPIRED_CERT
                else (self.tsa_certificate, self._tsa_key)
            )
            if self.signer is not None:
                cert, key = self.signer
            token = self._token(
                hash_name=imprint["hash_algorithm"]["algorithm"].native,
                hashed=hashed,
                nonce=request["nonce"].native,
                cert=cert,
                key=key,
                include_cert=bool(request["cert_req"].native),
            )
            response = tsp.TimeStampResp(
                {"status": {"status": "granted"}, "time_stamp_token": token}
            )
            return bytes(response.dump())

    def _token(
        self,
        *,
        hash_name: str,
        hashed: bytes,
        nonce: int | None,
        cert: x509.Certificate,
        key: PrivateKey,
        include_cert: bool,
    ) -> Any:
        cert_der = cert.public_bytes(serialization.Encoding.DER)
        asn1_cert = asn1_x509.Certificate.load(cert_der)
        gen_time = self._clock().astimezone(UTC).replace(microsecond=0)
        tst = tsp.TSTInfo(
            {
                "version": "v1",
                "policy": self.policy,
                "message_imprint": {
                    "hash_algorithm": {"algorithm": hash_name},
                    "hashed_message": hashed,
                },
                "serial_number": secrets.randbits(63),
                "gen_time": gen_time,
                "accuracy": {"seconds": 1},
                "nonce": nonce,
                "tsa": asn1_x509.GeneralName({"directory_name": asn1_cert.subject}),
            }
        )
        tst_der = tst.dump()
        signed_attrs = cms.CMSAttributes(
            [
                cms.CMSAttribute({"type": "content_type", "values": ["tst_info"]}),
                cms.CMSAttribute(
                    {"type": "message_digest", "values": [hashlib.sha256(tst_der).digest()]}
                ),
                cms.CMSAttribute(
                    {
                        "type": "signing_certificate_v2",
                        "values": [
                            {
                                "certs": [
                                    {
                                        "hash_algorithm": {"algorithm": "sha256"},
                                        "cert_hash": hashlib.sha256(cert_der).digest(),
                                    }
                                ]
                            }
                        ],
                    }
                ),
            ]
        )
        to_sign = signed_attrs.dump()
        signature_parameters: dict[str, Any] | None = None
        if isinstance(key, rsa.RSAPrivateKey) and self.rsa_pss:
            signature = key.sign(
                to_sign, padding.PSS(padding.MGF1(hashes.SHA256()), 32), hashes.SHA256()
            )
            signature_algorithm = "rsassa_pss"
            signature_parameters = {
                "hash_algorithm": {"algorithm": "sha256"},
                "mask_gen_algorithm": {
                    "algorithm": "mgf1",
                    "parameters": {"algorithm": "sha256"},
                },
                "salt_length": 32,
                "trailer_field": "trailer_field_bc",
            }
        elif isinstance(key, rsa.RSAPrivateKey):
            signature = key.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())
            signature_algorithm = "rsassa_pkcs1v15"
        else:
            signature = key.sign(to_sign, ec.ECDSA(hashes.SHA256()))
            signature_algorithm = "sha256_ecdsa"
        if self.failure is MockFailure.BAD_SIGNATURE:
            signature = bytes([signature[0] ^ 0x01]) + signature[1:]
        signer_info = cms.SignerInfo(
            {
                "version": "v1",
                "sid": cms.SignerIdentifier(
                    {
                        "issuer_and_serial_number": {
                            "issuer": asn1_cert.issuer,
                            "serial_number": asn1_cert.serial_number,
                        }
                    }
                ),
                "digest_algorithm": {"algorithm": "sha256"},
                "signed_attrs": signed_attrs,
                "signature_algorithm": (
                    {"algorithm": signature_algorithm}
                    if signature_parameters is None
                    else {"algorithm": signature_algorithm, "parameters": signature_parameters}
                ),
                "signature": signature,
            }
        )
        signed_data = cms.SignedData(
            {
                "version": "v3",
                "digest_algorithms": [{"algorithm": "sha256"}],
                "encap_content_info": {
                    "content_type": "tst_info",
                    "content": cms.ParsableOctetString(tst_der),
                },
                "certificates": [asn1_cert] if include_cert else [],
                "signer_infos": [signer_info],
            }
        )
        return cms.ContentInfo({"content_type": "signed_data", "content": signed_data})


def _rejection(text: str, fail_info: str) -> bytes:
    response = TimeStampResp(
        {"status": {"status": "rejection", "status_string": [text], "fail_info": {fail_info}}}
    )
    return bytes(response.dump())


class MockTsaProvider(Rfc3161Client):
    """In-process provider used by tests and the demo mode."""

    name = "mock"
    legal_grade = False

    def __init__(self, tsa: MockTsa, *, digest: str = "sha256") -> None:
        super().__init__(digest)
        self.tsa = tsa

    def _exchange(self, request: bytes) -> bytes:
        if self.tsa.failure is MockFailure.UNREACHABLE:
            raise TsaUnavailableError("mock TSA unreachable (injected)")
        if self.tsa.failure is MockFailure.TIMEOUT:
            raise TsaUnavailableError("mock TSA timed out (injected)")
        return self.tsa.respond(request)

    def credits(self) -> int | None:
        return self.tsa.credits


class MockTsaServer:
    """HTTP front end for :class:`MockTsa` on 127.0.0.1 (random port)."""

    def __init__(self, tsa: MockTsa, *, delay_seconds: float = 0.0) -> None:
        self.tsa = tsa
        self.delay_seconds = delay_seconds
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.headers.get("Content-Type") != "application/timestamp-query":
                    self.close_connection = True  # like Kamu SM's WAF: reset, no answer
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if outer.delay_seconds:
                    threading.Event().wait(outer.delay_seconds)
                answer = outer.tsa.respond(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/timestamp-reply")
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}/"

    def __enter__(self) -> MockTsaServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
