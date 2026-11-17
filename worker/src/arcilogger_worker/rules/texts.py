"""Names, alert titles and advice of the security rules, Turkish first (Wave 6 contract §2.2).

A title is a template whose fields come from the hit: ``{key}`` (the dedup group) and
``{count}`` (rows behind the hit) always, the others from ``Hit.values`` (:func:`title`). The
fields of a rule's template are what its package's ``values`` must hold (a test compares them).
Titles name an IP address, a device MAC, a source or a device account, **never a person** (contract
§0.1 rule 5: a person-level rule keeps ``details.person_id`` only), and never quote a log line
(R07: the virus name and the source address only; the file name and URL stay in the raw line).

The advice ("Ne yapılmalı" / "What to do") is what the rule editor and the docs show. The rules'
thresholds are starting values, not measured facts (:data:`STARTING_VALUE`).
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

#: The note beside every threshold in the rule editor and the docs (contract §0.1 rule 9).
STARTING_VALUE: Final = (
    "Başlangıç değeri; ağınıza göre ayarlayın.",
    "A starting value; adjust it to your network.",
)
#: The fields every hit gives a title, besides its rule's ``values``.
COMMON_FIELDS: Final = frozenset({"key", "count"})


@dataclass(frozen=True)
class RuleTexts:
    name_tr: str
    name_en: str
    #: Templates with ``{field}`` placeholders (:func:`title_fields`).
    title_tr: str
    title_en: str
    advice_tr: str
    advice_en: str


def title_fields(texts: RuleTexts) -> frozenset[str]:
    """The fields the two title templates use. Both use the same ones, except a field of one
    language (``reason_tr`` in Turkish, ``reason_en`` in English)."""

    def fields(template: str) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(template) if name}

    def common(names: set[str], suffix: str) -> set[str]:
        return {name.removesuffix(suffix) for name in names}

    turkish, english = fields(texts.title_tr), fields(texts.title_en)
    if any(name.endswith("_en") for name in turkish) or any(
        name.endswith("_tr") for name in english
    ):
        raise ValueError("a title uses the other language's field")
    if common(turkish, "_tr") != common(english, "_en"):
        difference = common(turkish, "_tr") ^ common(english, "_en")
        raise ValueError(f"title fields differ: {sorted(difference)}")
    return frozenset(turkish | english)


def title(rule_id: str, key: str, count: int, values: Mapping[str, object]) -> tuple[str, str]:
    """The Turkish and English title of a hit of ``rule_id``; ``KeyError`` names a missing
    field."""
    texts = TEXTS[rule_id]
    fields = {**values, "key": key, "count": count}
    return texts.title_tr.format_map(fields), texts.title_en.format_map(fields)


TEXTS: Final[Mapping[str, RuleTexts]] = {
    "R01": RuleTexts(
        "İçeriden port taraması",
        "Internal port scan",
        "İç adres {key} {ports} farklı porta / {hosts} adrese engellendi",
        "Internal address {key} was blocked on {ports} ports / {hosts} addresses",
        "Adresin hangi cihaza ait olduğunu 5651 sorgusuyla bulun; cihazda zararlı yazılım ya da "
        "izinsiz tarama aracı olup olmadığını denetleyin, gerekirse cihazı ağdan ayırın.",
        "Find the device of the address with a 5651 lookup; check it for malware or an "
        "unauthorised scanning tool and take it off the network if need be.",
    ),
    "R02": RuleTexts(
        "Dışarıdan tarama",
        "External scan",
        "Dış adres {key} {count} kez engellendi",
        "External address {key} was blocked {count} times",
        "Engellenen bağlantıların hedef portlarına bakın; açık bırakılmış bir hizmet varsa "
        "kapatın. Süren taramalarda adresi güvenlik duvarında engelleyebilirsiniz.",
        "Look at the target ports of the blocked connections and close any service left open. "
        "For a scan that goes on, you can block the address on the firewall.",
    ),
    "R03": RuleTexts(
        "Kaba kuvvet giriş denemesi",
        "Brute-force login",
        "{key}: {count} başarısız yönetici girişi",
        "{key}: {count} failed admin logins",
        "Denemelerin geldiği adresi ve hesabı denetleyin; hesap gerçekse parolasını değiştirin, "
        "yönetim arayüzüne yalnızca yönetim ağından erişilmesine izin verin.",
        "Check the address and the account the attempts came from; change the password of a "
        "real account and allow the management interface from the management network only.",
    ),
    "R04": RuleTexts(
        "Mesai dışı / beklenmeyen yerden yönetici girişi",
        "Admin login off hours or from an unexpected address",
        "Yönetici girişi: {key}, {srcip} ({reason_tr})",
        "Admin login: {key}, {srcip} ({reason_en})",
        "Girişi yapan kişiye girişin kendisine ait olduğunu doğrulatın; değilse hesabın parolasını "
        "değiştirin ve cihazın yapılandırma değişikliklerini gözden geçirin.",
        "Have the account's owner confirm the login; if it was not theirs, change the "
        "account's password and review the device's configuration changes.",
    ),
    "R05": RuleTexts(
        "IPS saldırısı",
        "IPS attack",
        "IPS saldırısı: {attack} ({srcip})",
        "IPS attack: {attack} ({srcip})",
        "Hedef sistemin güncel olduğunu ve saldırının engellendiğini doğrulayın; engellenmediyse "
        "IPS profilini ve hedefin yamalarını gözden geçirin.",
        "Make sure the target system is up to date and that the attack was blocked; if it was "
        "not, review the IPS profile and the target's patches.",
    ),
    "R06": RuleTexts(
        "Botnet / tehdit IP",
        "Botnet or threat IP",
        "Botnet / tehdit IP bağlantısı: {key}",
        "Botnet or threat IP connection: {key}",
        "Adresin cihazını 5651 sorgusuyla bulun, ağdan ayırın ve zararlı yazılım taraması "
        "yapın; cihazın parolalarını değiştirin.",
        "Find the address's device with a 5651 lookup, take it off the network and scan it for "
        "malware; change the device's passwords.",
    ),
    "R07": RuleTexts(
        "Zararlı yazılım",
        "Malware",
        "Zararlı yazılım: {virus} ({srcip})",
        "Malware: {virus} ({srcip})",
        "Dosyanın engellendiğini doğrulayın; cihazda tam tarama yapın. Dosyanın adı ve adresi "
        "kanıt satırındadır (Ham satırı aç).",
        "Make sure the file was blocked and run a full scan on the device. The file's name and "
        "address are in the evidence line (open the raw line).",
    ),
    "R08": RuleTexts(
        "DNS atlatma",
        "DNS bypass",
        "DNS atlatma: {key}, izinli olmayan sunuculara {count} sorgu",
        "DNS bypass: {key}, {count} queries to servers that are not allowed",
        "Cihazın DNS ayarını ve tarayıcısının güvenli DNS (DoH/DoT) ayarını denetleyin; dış DNS "
        "sunucularına giden 53 ve 853 numaralı portları güvenlik duvarında kapatmayı düşünün.",
        "Check the device's DNS setting and its browser's secure DNS (DoH/DoT) setting; consider "
        "closing ports 53 and 853 to outside DNS servers on the firewall.",
    ),
    "R09": RuleTexts(
        "Engelli kategori ısrarı",
        "Repeated blocked categories",
        "Engelli kategoriye {count} istek: {srcip}",
        "{count} requests to blocked categories: {srcip}",
        "İsteklerin bir uygulamadan mı yoksa kişiden mi geldiğine bakın; kurum kurallarına göre "
        "işlem yapın. Kişi adları yalnızca yetkili rollere gösterilir.",
        "See whether the requests came from an application or a person and act under your "
        "organisation's rules. Person names are shown to authorised roles only.",
    ),
    "R10": RuleTexts(
        "Proxy / VPN / Tor",
        "Proxy / VPN / Tor",
        "Proxy / VPN / Tor kullanımı: {key} ({what})",
        "Proxy, VPN or Tor use: {key} ({what})",
        "Bu kullanım filtreyi ve kayıtları atlatabilir; cihazı bulun, kurum kurallarına göre işlem "
        "yapın ve ilgili uygulama ya da kategoriyi engelleyin.",
        "This use can get around the filter and the logs; find the device, act under your "
        "organisation's rules and block the application or category.",
    ),
    "R11": RuleTexts(
        "Yeni cihaz",
        "New device",
        "Yeni cihaz: {key}",
        "New device: {key}",
        "Cihazın tanıdık olup olmadığını denetleyin; tanıdıksa Cihazlar sayfasında bir kişiye "
        "atayın, değilse bağlandığı yeri bulun.",
        "Check whether the device is known; if it is, assign it to a person on the Devices page, "
        "if not, find where it is connected.",
    ),
    "R12": RuleTexts(
        "MAC değişimi",
        "MAC change",
        "MAC değişimi: {key} adresinde {count} değişim",
        "MAC change: {count} changes on {key}",
        "Aynı adresi sırayla kullanan cihazları bulun; adres çakışması, yanlış sabit adres ya da "
        "ARP zehirlemesi olabilir.",
        "Find the devices that use the address in turn; it may be an address conflict, a wrong "
        "static address or ARP poisoning.",
    ),
    "R13": RuleTexts(
        "Büyük veri gönderimi",
        "Large upload",
        "Büyük veri gönderimi: {srcip}, {mib} MiB",
        "Large upload: {srcip}, {mib} MiB",
        "Gönderimin hedefini ve bilinen bir yedekleme ya da eşitleme olup olmadığını denetleyin; "
        "değilse veri sızıntısı olasılığını inceleyin.",
        "Check the target of the upload and whether it is a known backup or sync; if not, look "
        "into a possible data leak.",
    ),
    "R14": RuleTexts(
        "Gece etkinliği",
        "Night activity",
        "Gece etkinliği: {srcip}, {mib} MiB",
        "Night activity: {srcip}, {mib} MiB",
        "Gece çalışan güncelleme ya da yedekleme olup olmadığını denetleyin; değilse cihazı ve "
        "trafiğin hedefini inceleyin.",
        "Check for an update or backup that runs at night; if there is none, look into the "
        "device and the target of the traffic.",
    ),
    "R16": RuleTexts(
        "Cihaz yapılandırma değişikliği",
        "Device configuration change",
        "Yapılandırma değişikliği: {key} ({count})",
        "Configuration change: {key} ({count})",
        "Değişikliğin planlı olduğunu doğrulayın; değilse cihazın yapılandırma yedeğiyle "
        "karşılaştırın ve hesabın parolasını değiştirin.",
        "Make sure the change was planned; if not, compare it with the device's configuration "
        "backup and change the account's password.",
    ),
    "R17": RuleTexts(
        "Güvenlik duvarı sağlığı",
        "Firewall health",
        "Güvenlik duvarı sağlığı: {key} ({reason_tr})",
        "Firewall health: {key} ({reason_en})",
        "Cihazın işlemci ve bellek kullanımına, oturum sayısına ve açık özelliklerine bakın; "
        "conserve mode trafiği keser ya da denetimsiz geçirir.",
        "Look at the device's CPU and memory use, its session count and the features turned on; "
        "conserve mode drops traffic or lets it through unchecked.",
    ),
    "R18": RuleTexts(
        "DHCP havuzu doldu",
        "DHCP pool exhausted",
        "DHCP havuzu doldu: {key}",
        "DHCP pool exhausted: {key}",
        "Havuzu genişletin ya da kira süresini kısaltın; havuzu dolduran bilinmeyen cihazlar "
        "varsa bulun.",
        "Enlarge the pool or shorten the lease time; find any unknown devices that fill it.",
    ),
    "R19": RuleTexts(
        "Şüpheli DNS (DGA)",
        "Suspicious DNS (DGA)",
        "Şüpheli DNS: {key}, {count} NXDOMAIN yanıtı",
        "Suspicious DNS: {key}, {count} NXDOMAIN answers",
        "Çok sayıda var olmayan alan adı sorgusu zararlı yazılım belirtisi olabilir; cihazı bulun "
        "ve tarayın.",
        "Many queries for names that do not exist can be a sign of malware; find the device and "
        "scan it.",
    ),
}
