"""Configuration file validation."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest
from pydantic import ValidationError

from arcilogger_core.config import (
    Config,
    ReplicaConfig,
    default_config_path,
    load_config,
    parse_config,
    replica_target_text,
    setup_problems,
)

MINIMAL = """
data_dir = "D:/ARCILOGGER"

[[sources]]
name = "fortigate"
type = "fortigate"
address = "172.31.255.1"
heartbeat_minutes = 10

[[sources]]
name = "core-r1"
type = "mikrotik"
address = "172.20.10.1"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "arcilogger.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_config_gets_safe_defaults(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, MINIMAL))
    assert config.retention.enabled is False
    assert config.retention.days == 760
    assert config.tsa.provider == "mock"
    assert config.tsa.legal_grade is False
    assert config.secrets_dir == tmp_path
    assert config.source_for("172.31.255.1") is not None
    assert config.source_for("172.31.255.99") is None


def test_retention_below_two_years_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="730"):
        load_config(_write(tmp_path, MINIMAL + "\n[retention]\ndays = 365\n"))


def test_replica_needs_a_target(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"replica\.target"):
        load_config(_write(tmp_path, MINIMAL + "\n[replica]\nenabled = true\n"))


@pytest.mark.parametrize(
    ("saved", "shown"),
    [
        ("\\\\NAS-01\\arcilogger-worm", "\\\\NAS-01\\arcilogger-worm"),
        ("\\\\NAS-01\\arcilogger-worm\\", "\\\\NAS-01\\arcilogger-worm"),
        ("//NAS-01/arcilogger-worm", "\\\\NAS-01\\arcilogger-worm"),
        ("//NAS-01/arcilogger-worm/", "\\\\NAS-01\\arcilogger-worm"),
        ("\\\\nas.ornek.example\\arci$\\gunluk", "\\\\nas.ornek.example\\arci$\\gunluk"),
        ("\\\\172.20.10.40\\worm\\a\\b\\", "\\\\172.20.10.40\\worm\\a\\b"),
    ],
)
def test_a_replica_share_reads_back_as_one_text_on_every_platform(saved: str, shown: str) -> None:
    """On Windows ``str(Path("\\\\NAS-01\\worm"))`` keeps the share root's separator
    (``\\\\NAS-01\\worm\\``, what ``PureWindowsPath`` gives here), a POSIX path keeps the text as
    saved: the target the settings page shows and an op expects is the same text either way."""
    windows = str(PureWindowsPath(saved))  # str(Path(saved)) on Windows
    assert replica_target_text(saved) == replica_target_text(windows) == shown
    for parsed in (Path(saved), Path(windows)):
        replica = ReplicaConfig(enabled=True, target=parsed)
        assert replica.model_dump(mode="json")["target"] == shown
        assert replica.target_text == shown
        assert replica.target == parsed  # the path the Replica task copies to is untouched


def test_a_replica_target_that_is_no_share_reads_back_as_its_path(tmp_path: Path) -> None:
    for target in (tmp_path / "nas", Path("\\\\NAS-01"), Path("\\\\?\\UNC\\NAS-01\\worm")):
        replica = ReplicaConfig(target=target)
        assert replica.model_dump(mode="json")["target"] == str(target)
    assert ReplicaConfig().model_dump(mode="json")["target"] is None
    assert ReplicaConfig().target_text is None


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ('[[sources]]\nname = "fortigate"\ntype = "generic"\naddress = "10.0.0.9"\n', "unique"),
        ('[[sources]]\nname = "x"\ntype = "generic"\naddress = "172.31.255.1"\n', "unique"),
        ('[[sources]]\nname = "unknown-x"\ntype = "generic"\naddress = "10.0.0.9"\n', "reserved"),
        ('[[sources]]\nname = "Bad Name"\ntype = "generic"\naddress = "10.0.0.9"\n', "pattern"),
        ('[[sources]]\nname = "x"\ntype = "generic"\naddress = "not-an-ip"\n', "IP"),
        ("bogus = 1\n", "Extra inputs"),
    ],
)
def test_invalid_sources_are_refused(tmp_path: Path, extra: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        load_config(_write(tmp_path, MINIMAL + "\n" + extra))


@pytest.mark.parametrize(
    ("provider", "url", "legal"),
    [
        ("zamane", "http://zd.kamusm.gov.tr", True),
        ("zamane", "http://tzd.kamusm.gov.tr", False),
        ("rfc3161", "http://zd.kamusm.gov.tr", False),
        ("mock", "http://zd.kamusm.gov.tr", False),
    ],
)
def test_only_kamusm_production_is_legal_grade(provider: str, url: str, legal: bool) -> None:
    config = Config.model_validate(
        {"data_dir": "/x", "secrets_dir": "/x", "tsa": {"provider": provider, "url": url}}
    )
    assert config.tsa.legal_grade is legal


def test_default_config_path_honours_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCILOGGER_CONFIG", "/tmp/custom.toml")  # noqa: S108
    assert default_config_path() == Path("/tmp/custom.toml")  # noqa: S108
    monkeypatch.delenv("ARCILOGGER_CONFIG")
    assert default_config_path().name == "arcilogger.toml"


def test_network_sources_name_each_sender(tmp_path: Path) -> None:
    extra = '[[sources]]\nname = "ap"\ntype = "mikrotik"\naddress = "172.20.10.0/24"\n'
    config = load_config(_write(tmp_path, MINIMAL + "\n" + extra))
    assert config.source_name_for("172.31.255.1") == "fortigate"
    assert config.source_name_for("172.20.10.101") == "ap-172.20.10.101"
    assert config.source_name_for("172.20.12.1") is None
    assert config.source_name_for("not-an-ip") is None
    assert config.sources[2].is_network


def test_database_api_ingest_worker_defaults(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, MINIMAL))
    database = config.database
    assert (database.host, database.port, database.name, database.user) == (
        "127.0.0.1",
        5432,
        "arcilogger",
        "arcilogger",
    )
    assert database.sslmode is None
    assert database.timescale == "auto"
    assert (config.api.host, config.api.port) == ("127.0.0.1", 8000)
    assert config.api.secure_cookies is True
    assert config.api.forwarded_allow_ips == "127.0.0.1"
    assert (config.ingest.batch_rows, config.ingest.batch_seconds) == (5000, 0.5)
    assert config.worker.interval_seconds == 60


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ('[database]\npassword = "plain"', "Extra inputs are not permitted"),  # never in the file
        ("[database]\nport = 0", "greater than or equal to 1"),
        ('[database]\nsslmode = "sometimes"', "sslmode"),
        ('[database]\ntimescale = "maybe"', "timescale"),
        ('[database]\nname = "x; DROP"', "name"),
        ("[api]\nport = 70000", "less than or equal to 65535"),
        ("[ingest]\nbatch_rows = 0", "batch_rows"),
    ],
)
def test_invalid_service_sections_are_refused(tmp_path: Path, section: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        load_config(_write(tmp_path, MINIMAL + "\n" + section + "\n"))


def test_ipv6_network_sources_get_windows_safe_names(tmp_path: Path) -> None:
    extra = '[[sources]]\nname = "lab"\ntype = "generic"\naddress = "2001:db8::/64"\n'
    config = load_config(_write(tmp_path, MINIMAL + "\n" + extra))
    assert config.source_name_for("2001:db8::7") == "lab-2001-db8--7"


def test_source_transport_defaults_to_any_and_is_validated(tmp_path: Path) -> None:
    extra = (
        '[[sources]]\nname = "ap"\ntype = "mikrotik"\naddress = "172.20.10.0/24"\n'
        'transport = "tcp"\n'
    )
    config = load_config(_write(tmp_path, MINIMAL + "\n" + extra))
    fortigate = config.source_config_for("172.31.255.1")
    assert fortigate is not None
    assert fortigate.transport == "any"
    network = config.source_config_for("172.20.10.7")
    assert network is not None
    assert (network.name, network.transport) == ("ap", "tcp")
    assert config.source_config_for("172.20.12.1") is None
    assert config.source_config_for("not-an-ip") is None
    bad = extra.replace('"tcp"', '"sctp"')
    with pytest.raises(ValidationError, match="transport"):
        load_config(_write(tmp_path, MINIMAL + "\n" + bad))


def test_smtp_is_off_by_default_and_complete_when_on(tmp_path: Path) -> None:
    smtp = load_config(_write(tmp_path, MINIMAL)).smtp
    assert (smtp.enabled, smtp.port, smtp.security, smtp.timeout_seconds) == (
        False,
        587,
        "starttls",
        30,
    )
    assert smtp.host is None
    assert smtp.ca_file is None
    on = (
        '[smtp]\nenabled = true\nhost = "mail.ornek.example"\nusername = "arcilogger"\n'
        'from_address = "arcilogger@ornek.example"\nsecurity = "ssl"\nport = 465\n'
    )
    smtp = load_config(_write(tmp_path, MINIMAL + "\n" + on)).smtp
    assert (smtp.host, smtp.port, smtp.security) == ("mail.ornek.example", 465, "ssl")


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ("[smtp]\nenabled = true", r"smtp\.host and smtp\.from_address"),
        ('[smtp]\nenabled = true\nhost = "mail.example.com"', "from_address"),
        ('[smtp]\npassword = "plain"', "Extra inputs are not permitted"),  # never in the file
        ('[smtp]\nsecurity = "tls-maybe"', "security"),
        ('[smtp]\nhost = "mail example.com"', "host"),
        ('[smtp]\nfrom_address = "ARCILOGGER <a@example.com>"', "from_address"),
        ("[smtp]\ntimeout_seconds = 0", "timeout_seconds"),
    ],
)
def test_invalid_smtp_sections_are_refused(tmp_path: Path, section: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        load_config(_write(tmp_path, MINIMAL + "\n" + section + "\n"))


# -- values every organisation sets itself: no customer's name or address as a default ----------


def test_organisation_name_and_local_time_source_are_empty_by_default(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, MINIMAL))
    assert config.site_name == ""
    assert config.site_name_set is False
    assert config.clock.ntp_server == ""
    assert config.clock.fallback_server == "time.ume.tubitak.gov.tr"
    assert config.clock.servers == ("time.ume.tubitak.gov.tr",)
    assert [(p.level, p.key) for p in setup_problems(config)] == [
        ("error", "site_name"),
        ("warning", "clock.ntp_server"),
    ]
    for problem in setup_problems(config):
        assert "[" in problem.message  # "Türkçe [English]"
    assert "-SiteName" in setup_problems(config)[0].message
    assert "-NtpServer" in setup_problems(config)[1].message


def test_a_complete_configuration_has_no_setup_problems(tmp_path: Path) -> None:
    text = 'site_name = "  Örnek A.Ş.  "\n' + MINIMAL + '\n[clock]\nntp_server = " 172.20.10.1 "\n'
    config = load_config(_write(tmp_path, text))
    assert (config.site_name, config.site_name_set) == ("Örnek A.Ş.", True)
    assert config.clock.servers == ("172.20.10.1", "time.ume.tubitak.gov.tr")
    assert setup_problems(config) == []


def test_no_log_source_is_a_setup_warning(tmp_path: Path) -> None:
    """A new installation starts without [[sources]] (the example lists its devices as comments
    only): a valid configuration, every sender is unknown, and config-check says what to add."""
    text = 'site_name = "Örnek A.Ş."\ndata_dir = "D:/ARCILOGGER"\n'
    config = load_config(_write(tmp_path, text + '[clock]\nntp_server = "10.0.0.1"\n'))
    assert config.sources == ()
    assert config.source_name_for("172.31.255.1") is None
    (problem,) = setup_problems(config)
    assert (problem.level, problem.key) == ("warning", "sources")
    assert problem.message.startswith("Log kaynağı ([[sources]]) tanımlanmamış")
    assert "[no log source ([[sources]]) is configured" in problem.message  # "Türkçe [English]"
    assert "docs/DEVICE-SETUP.md" in problem.message


def test_no_time_server_at_all_is_a_setup_error(tmp_path: Path) -> None:
    text = 'site_name = "Örnek A.Ş."\n' + MINIMAL + '\n[clock]\nfallback_server = ""\n'
    config = load_config(_write(tmp_path, text))
    assert config.clock.servers == ()
    assert [(p.level, p.key) for p in setup_problems(config)] == [
        ("error", "clock.fallback_server")
    ]


@pytest.mark.parametrize(
    ("top", "section", "message"),
    [
        ('site_name = "Örnek\\nA.Ş."', "", "single line"),
        ('site_name = "Örnek\\u200eA.Ş."', "", "single line"),  # a bidi mark (format character)
        (f'site_name = "{"x" * 201}"', "", "at most 200"),
        ("", '[clock]\nntp_server = "172.20.10.1 time.example"', "not a host name"),
        ("", f'[clock]\nfallback_server = "{"a" * 254}"', "at most 253"),
    ],
)
def test_invalid_organisation_name_or_time_server_is_refused(
    tmp_path: Path, top: str, section: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        load_config(_write(tmp_path, top + "\n" + MINIMAL + "\n" + section + "\n"))


def test_a_source_is_enabled_unless_switched_off(tmp_path: Path) -> None:
    """``enabled`` is new (ADR-0014): every existing file stays valid and its sources on."""
    config = load_config(_write(tmp_path, MINIMAL))
    assert all(source.enabled for source in config.sources)
    text = MINIMAL.replace(
        'address = "172.20.10.1"\n', 'address = "172.20.10.1"\nenabled = false\n'
    )
    off = load_config(_write(tmp_path, text))
    core = off.source_for("172.20.10.1")
    assert core is not None
    assert core.enabled is False
    assert off.source_name_for("172.20.10.1") == "core-r1", "still recorded under its name"
    assert type(core).model_validate(core.model_dump()) == core
    with pytest.raises(ValidationError, match="enabled"):
        load_config(_write(tmp_path, text.replace("enabled = false", 'enabled = "maybe"')))


def test_the_admin_folder_lies_next_to_the_secret_store(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, MINIMAL))
    assert config.admin_dir == tmp_path / "admin"
    with pytest.raises(ValidationError, match="admin_dir"):
        load_config(_write(tmp_path, 'admin_dir = "D:/elsewhere"\n' + MINIMAL))


def test_parse_config_reads_bytes_as_load_config_reads_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path, MINIMAL)
    assert parse_config(path.read_bytes(), path) == load_config(path)
    with pytest.raises(ValueError, match="utf-8"):
        parse_config(MINIMAL.encode("utf-16"), path)
