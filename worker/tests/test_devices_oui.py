"""Device helpers without a database: randomised MACs, the optional OUI file, sighting summary,
explanation texts."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from arcilogger_worker.correlation.devices import DeviceSighting, summarize
from arcilogger_worker.correlation.oui import OuiTable, load_oui
from arcilogger_worker.lookup.texts import ENGINE_EXPLANATIONS, explain
from arcilogger_worker.macaddr import is_randomized
from arcilogger_worker.testing import tr

DAY = date(2026, 10, 12)


@pytest.mark.parametrize(
    ("mac", "randomized"),
    [
        ("02:00:5E:00:53:46", True),  # a phone's private (randomised) address
        ("02:00:00:00:00:01", True),
        ("0a-00-00-00-00-01", True),
        ("2E00.0000.0001", True),
        ("00:00:5E:00:53:31", False),  # a manufacturer (universally administered) address
        ("00:00:5e:00:53:32", False),
        ("03:00:00:00:00:01", False),  # multicast bit set: not a device address
        ("01:00:5e:00:00:01", False),
    ],
)
def test_randomized_is_the_locally_administered_unicast_bit(mac: str, randomized: bool) -> None:
    assert is_randomized(mac) is randomized


def test_randomized_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="invalid MAC"):
        is_randomized("02:00:5e")


def test_ieee_csv_with_longest_prefix_match() -> None:
    table = OuiTable.parse(
        "Registry,Assignment,Organization Name,Organization Address\n"
        'MA-L,F01FAF,"Dell, Inc.",Round Rock TX US\n'
        "MA-M,F01FAF1,Small Vendor,Somewhere\n"
        "MA-S,F01FAF123,Tiny Vendor,Elsewhere\n"
        "MA-L,not-hex,Broken,\n"
    )
    assert len(table) == 3
    assert table.skipped_lines == 1
    assert table.lookup("f0:1f:af:00:00:01") == "Dell, Inc."
    assert table.lookup("F0-1F-AF-10-00-01") == "Small Vendor"
    assert table.lookup("f01f.af12.3456") == "Tiny Vendor"
    assert table.lookup("00:00:00:00:00:00") is None
    assert table.lookup("f0:1f") is None


def test_ieee_text_listing_and_simple_formats() -> None:
    text = (
        "OUI/MA-L                                                    Organization\n"
        "company_id                                                  Organization\n"
        "                                                            Address\n"
        "\n"
        "00-00-0C   (hex)\t\tCisco Systems, Inc\n"
        "00000C     (base 16)\t\tCisco Systems, Inc\n"
        "\t\t\t\t170 WEST TASMAN DRIVE\n"
        "# a comment\n"
        # Made-up vendor names; 00:00:5E holds the RFC 7042 documentation addresses.
        "00:00:5E\tExample Docs, Inc.\n"
        "AC-DE-48,Example Networks, Inc.\n"
        "00:1B:C5:00:00:00/36\tConverging\tConverging Systems Inc.\n"
    )
    table = OuiTable.parse(text)
    assert table.lookup("00:00:0c:12:34:56") == "Cisco Systems, Inc"
    assert table.lookup("00:00:5e:00:53:31") == "Example Docs, Inc."
    assert table.lookup("ac:de:48:00:53:32") == "Example Networks, Inc."
    assert table.lookup("00:1b:c5:00:01:23") == "Converging Systems Inc."
    assert table.skipped_lines == 2  # the two header lines


def test_load_oui_is_optional_and_follows_file_changes(tmp_path: Path) -> None:
    assert load_oui(None) is None
    assert load_oui("  ") is None
    assert load_oui(tmp_path / "missing.csv") is None
    path = tmp_path / "oui.txt"
    path.write_text("00:00:5E\tExample Docs\n", encoding="utf-8")
    first = load_oui(str(path))
    assert first is not None
    assert first.lookup("00:00:5e:00:53:01") == "Example Docs"
    assert load_oui(path) is first  # cached
    path.write_text("00:00:5E\tExample Docs Systems\n", encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    second = load_oui(path)
    assert second is not None
    assert second.lookup("00:00:5e:00:53:01") == "Example Docs Systems"


def test_unreadable_oui_path_is_no_vendor_and_logged_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    folder = tmp_path / "oui"  # e.g. the setting points at the folder, not the file
    folder.mkdir()
    with caplog.at_level("WARNING"):
        assert load_oui(folder) is None
        assert load_oui(str(folder)) is None
    warnings = [r for r in caplog.records if "cannot be read" in r.getMessage()]
    assert len(warnings) == 1


def test_summarize_sightings_per_mac() -> None:
    mac = "F0:1F:AF:00:00:01"
    entries = summarize(
        [
            DeviceSighting(mac, tr(DAY, 9), ip="172.20.43.121", vlan=43, hostname="PC-07"),
            DeviceSighting(mac.lower(), tr(DAY, 8), ip="172.20.43.120", vlan=43),
            DeviceSighting(mac, tr(DAY, 10), hostname="PC-07"),
            DeviceSighting(mac, tr(DAY, 11), ip="172.20.41.8", vlan=41, hostname=" pc-07b "),
        ]
    )
    (entry,) = entries
    assert (entry.mac, entry.first, entry.last) == ("f0:1f:af:00:00:01", tr(DAY, 8), tr(DAY, 11))
    assert (entry.ip, entry.vlan) == ("172.20.41.8", 41)
    assert entry.hostnames == ["PC-07", "pc-07b"]


def test_explanation_texts_are_filled_and_bilingual() -> None:
    note = explain(
        "no_lease", ip="172.20.43.9", at=(tr(DAY, 9)).isoformat(), before=None, after=None
    )
    # The whole 2.11 text: device roles and the Sources page, never a host name of one network.
    assert note.text_tr == (
        "172.20.43.9 adresi için 12.10.2026 09:00:00 anında geçerli bir kira kaydı yok. En yakın "
        "kayıtlar: önce —, sonra —. Kira kanıtı DHCP sunucusunun kayıtlarından (MikroTik DHCP ya "
        "da FortiGate DHCP olayları) ve MikroTik ARP-LOG kayıtlarından gelir: DHCP sunucusunun "
        'kayıt gönderdiğini Kaynaklar sayfasındaki "Kira kanıtı" kutusunda kontrol edin. Statik '
        "IP'li cihazlar yalnızca ARP-LOG ile (ya da srcmac ile kısmi kanıt olarak) görünür."
    )
    assert note.text_en == (
        "No lease covers 172.20.43.9 at 12.10.2026 09:00:00. Nearest leases: before —, after —. "
        "Lease evidence comes from the DHCP server's records (MikroTik DHCP or FortiGate DHCP "
        'events) and from MikroTik ARP-LOG lines: check on the Sources page ("Lease evidence") '
        "that the DHCP server sends them. Static-IP devices only appear through ARP-LOG (or, as "
        "partial evidence, through srcmac)."
    )
    mac = explain("no_lease_for_mac", mac="00:00:5e:00:53:57", at=(tr(DAY, 9)).isoformat())
    assert mac.text_tr == (
        "00:00:5e:00:53:57 için 12.10.2026 09:00:00 anında geçerli bir kira kaydı (DHCP ya da "
        "ARP-LOG) yok."
    )
    assert mac.text_en == (
        "No lease (DHCP or ARP-LOG) of 00:00:5e:00:53:57 covers 12.10.2026 09:00:00."
    )
    day = explain("not_in_database", day=(DAY - timedelta(days=1)).isoformat())
    assert day.text_tr.startswith("11.10.2026 gününe ait kayıt veritabanında yok")
    assert day.params == {"day": "2026-10-11"}
    standard = explain("double_nat")
    assert standard.text_en.startswith("An exact match needs the ISP router's NAT record")
    for text_tr, text_en in ENGINE_EXPLANATIONS.values():
        assert text_tr != text_en
        assert "yasal" not in text_tr.lower()  # never claims legal compliance
    with pytest.raises(KeyError):
        explain("no_such_key")
