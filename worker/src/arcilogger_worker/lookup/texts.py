"""Explanation texts of the lookup engine (Turkish first, English second).

The standard texts of the contract (``double_nat`` — spec 12.4 wording —, ``exact_nat``,
``no_match``, ``only_in_vault``, ``ip_conflict``, ``guest_vlan``) live in
:data:`arcilogger_worker.lookup.types.EXPLANATIONS`; the engine adds the ones below. Texts are
neutral and never claim legal compliance. ``{name}`` placeholders are filled from the
explanation's ``params`` (times are shown as ``12.10.2026 18:19:07`` Europe/Istanbul; ``params``
keep the machine form: UTC ISO-8601 strings and integers).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Final

from arcilogger_core.fmt import format_date, format_datetime
from arcilogger_worker.lookup.types import EXPLANATIONS, Explanation

ENGINE_EXPLANATIONS: Final[Mapping[str, tuple[str, str]]] = {
    "no_lease": (
        "{ip} adresi için {at} anında geçerli bir DHCP/ARP kaydı (kira) yok. En yakın kayıtlar: "
        "önce {before}, sonra {after}. DHCP ve saatlik ARP-LOG kayıtlarının ana "
        "yönlendiriciden (MikroTik, DHCP sunucusu) geldiğini kontrol edin; statik IP'li "
        "cihazlar yalnızca ARP-LOG ile görünür.",
        "No DHCP/ARP lease covers {ip} at {at}. Nearest leases: before {before}, after "
        "{after}. Check that the main router (MikroTik, the DHCP server) sends DHCP and hourly "
        "ARP-LOG messages; static-IP devices only appear through ARP-LOG.",
    ),
    "no_match_exact": (
        "FortiGate bu zaman aralığında {address}:{port} için oturum kaydetmemiş. Saati ve saat "
        "dilimini, protokolü ve toleransı kontrol edin; FortiGate politikalarında trafik "
        "kaydının (logtraffic all) açık olduğundan emin olun.",
        "The FortiGate logged no session for {address}:{port} in this window. Check the time "
        "and time zone, the protocol and the tolerance; make sure the FortiGate policies log "
        "all traffic (logtraffic all).",
    ),
    "no_match_candidates": (
        "Bu aralıkta aday oturum yok. Sonucu kesinleştirmek için: ISS modeminin NAT kaydı "
        "(genel port → FortiGate çıkış portu), talepteki hedef IP/port veya alan adı ve doğru "
        "saat/saat dilimi gerekir; toleransı genişletmek de yardımcı olabilir.",
        "No candidate session in this window. An exact answer needs the ISP router's NAT "
        "record (public port → FortiGate port), the requested destination IP/port or domain "
        "and the correct time/time zone; a wider tolerance may also help.",
    ),
    "candidates_without_destination": (
        "Talepte hedef IP/port veya alan adı yok: aralıktaki tüm oturumlar yalnızca zamana "
        "göre sıralandı. Hedef bilgisi aday sayısını büyük ölçüde azaltır.",
        "The request names no destination IP/port or domain: all sessions in the window are "
        "ranked by time only. A destination narrows the candidates considerably.",
    ),
    "destination_not_found": (
        "İstenen hedefe ({destination}) giden oturum bulunamadı; başka oturumlar aday olarak "
        "listelenmez. Hedef bilgisini, saati ve saat dilimini kontrol edin. Hedeften bağımsız "
        "olarak aralıktaki tüm oturumları görmek için sorguyu hedef alanlarını boş bırakarak "
        "yeniden çalıştırın.",
        "No session to the requested destination ({destination}) was found; other sessions "
        "are not listed as candidates. Check the destination, the time and the time zone. To "
        "see every session of the window regardless of the destination, run the lookup again "
        "with the destination fields empty.",
    ),
    "double_nat_no_candidates": (
        "Kesin eşleşme için ISS modeminin NAT kaydı gerekir; bu zaman aralığında listelenecek "
        "aday oturum da bulunamadı.",
        "An exact match needs the ISP router's NAT record; there is also no candidate session "
        "to list in this time window.",
    ),
    "port_missing": (
        "Genel IP FortiGate'in kendi adresi, ancak port verilmediği için kesin eşleşme "
        "yapılamaz; zaman penceresindeki oturumlar aday olarak aranır.",
        "The public IP is the FortiGate's own address, but without a port no exact match is "
        "possible; the sessions in the window are searched as candidates.",
    ),
    "public_ip_not_configured": (
        "{address} adresi FortiGate için tanımlı genel/WAN adreslerinden biri değil "
        "(Ayarlar → NAT); bu nedenle yalnızca aday araması yapılabilir.",
        "{address} is not one of the FortiGate's configured public/WAN addresses "
        "(Settings → NAT), so only a candidate search is possible.",
    ),
    "port_hint": (
        "Sıralamada FortiGate çıkış portunun talepteki portla aynı olması da dikkate alındı. "
        "Bu, ISS modeminin kaynak portu koruduğu varsayımına dayanır ve sahada "
        "doğrulanmalıdır.",
        "The ranking also favours sessions whose FortiGate port equals the requested port. "
        "This assumes the ISP router keeps source ports and must be verified on site.",
    ),
    "multiple_exact": (
        "Aynı genel adres ve port bu aralıkta {count} farklı oturumda kullanılmış; hepsi "
        "listelenmiştir.",
        "The same public address and port were used by {count} sessions in this window; all "
        "are listed.",
    ),
    "exact_other_destinations": (
        "{address}:{port} bu aralıkta ayrıca {count} oturumda kullanılmış (port yeniden "
        "kullanımı); FortiGate kayıtlarına göre bu oturumların hedef IP adresi veya portu "
        "talepteki hedeften farklı olduğu için listelenmez.",
        "{address}:{port} was also used by {count} other sessions in this window (port reuse); "
        "the FortiGate logs show a destination IP or port other than the requested one, so they "
        "are not listed.",
    ),
    "exact_only_other_destinations": (
        "{address}:{port} bu aralıkta {count} oturumda kullanılmış; FortiGate kayıtlarına göre "
        "bu oturumların hiçbirinin hedef IP adresi veya portu talepteki hedefle aynı değil, bu "
        "nedenle listelenmez.",
        "{address}:{port} was used by {count} sessions in this window; the FortiGate logs show "
        "a destination IP or port other than the requested one for each of them, so they are "
        "not listed.",
    ),
    "exact_domain_unverified": (
        "NAT adresi ve portu oturumu belirler; ancak talepteki alan adının ({domain}) bu "
        "oturumun hedefi olduğu FortiGate DNS, web filtresi veya uygulama kayıtlarından "
        "gösterilemedi (ad başka bir DNS sunucusunda veya şifreli DNS ile çözülmüş ya da "
        "önbellekten kullanılmış olabilir). Hedef doğrulanmadı; oturumun hedef IP adresini "
        "talepteki bilgiyle karşılaştırın.",
        "The NAT address and port identify the session, but the FortiGate DNS, web filter and "
        "application logs do not show that the requested domain ({domain}) was its destination "
        "(the name may have been resolved by another DNS server, over encrypted DNS, or from a "
        "cache). The destination is not verified; compare the session's destination IP with "
        "the request.",
    ),
    "exact_unverified_destinations": (
        "{address}:{port} bu aralıkta ayrıca {count} oturumda kullanılmış; bu oturumların "
        "talepteki alan adıyla ({domain}) ilişkisi kayıtlardan gösterilemedi. Alan adına "
        "kayıtlarla bağlanan oturum bulunduğu için bunlar listelenmez.",
        "{address}:{port} was also used by {count} other sessions in this window; the logs do "
        "not tie them to the requested domain ({domain}). Since a session tied to the domain by "
        "the logs exists, they are not listed.",
    ),
    "public_ip_not_transip": (
        "Talepteki {address} adresi bu zaman aralığında FortiGate kayıtlarında çıkış (kaynak "
        "NAT) adresi olarak görünmüyor; kayıtlarda başka bir çıkış adresi var. ISS modemi bu "
        "tarihte hâlâ NAT yapıyor olabilir (çift NAT) veya Ayarlar → NAT değeri bu tarih için "
        "doğru değil. Bu nedenle kesin eşleşme yerine aday araması yapıldı.",
        "The requested address {address} does not appear as the FortiGate's source-NAT address "
        "in its logs around this moment; the logs show another address. The ISP router may "
        "still have translated at that date (double NAT), or the NAT setting does not apply to "
        "that date. A candidate search was therefore made instead of an exact match.",
    ),
    "wan_transip_not_seen": (
        "Ayarlardaki FortiGate WAN adresi ({address}) bu zaman aralığında FortiGate "
        "kayıtlarında çıkış (kaynak NAT) adresi olarak görünmüyor; kayıtlarda başka bir çıkış "
        "adresi var. Aday araması bu adresle sınırlandırılmadan tüm kaynak NAT oturumlarında "
        "yapıldı. Ayarlar → NAT değerinin bu tarih için doğru olduğunu kontrol edin.",
        "The configured FortiGate WAN address ({address}) does not appear as the FortiGate's "
        "source-NAT address in its logs around this moment; the logs show another address. The "
        "candidate search was made over all source-NAT sessions instead of being narrowed to "
        "it. Check that the NAT setting applies to that date.",
    ),
    "person_link_at_lookup_time": (
        "Kişi ve departman, sorgunun yapıldığı andaki cihaz-kişi atamasını ve kişinin o andaki "
        "departmanını gösterir; cihazın olay anında kimde olduğunun kanıtı değildir (bu cihaz "
        "için atama geçmişi kaydı yoktur).",
        "Person and department are the device-person assignment and the person's department "
        "at lookup time; they are not evidence of who held the device at the time of the event "
        "(there is no assignment history record for this device).",
    ),
    "person_department_current": (
        "Kişi, olay anında geçerli olan cihaz-kişi atamasıdır (atama geçmişi). Departman ise "
        "kişinin bugünkü departmanıdır; departman değişikliklerinin geçmişi tutulmaz.",
        "The person is the device-person assignment valid at the time of the event (assignment "
        "history). The department is the person's current one; department changes are not "
        "kept as history.",
    ),
    "person_link_backfilled": (
        "Bu cihaz-kişi ataması, atama geçmişi tutulmaya başlanmadan önce girilmişti; "
        "başlangıcı olarak cihazın ilk görüldüğü an ({assigned_from}) alınmıştır. Atamanın "
        "gerçek başlangıç tarihi kayıtlı değildir.",
        "This device-person assignment was entered before assignment history was kept; the "
        "moment the device was first seen ({assigned_from}) is used as its start. The real "
        "start of the assignment is not recorded.",
    ),
    "person_unassigned_at_time": (
        "Atama geçmişine göre cihaz {at} anında hiçbir kişiye atanmış değildi; bu nedenle kişi "
        "gösterilmez (departman, varsa, VLAN'dan türetilir).",
        "According to the assignment history the device was not assigned to anybody at {at}, so "
        "no person is shown (the department, if any, is derived from the VLAN).",
    ),
    "truncated": (
        "{total} aday bulundu; en iyi {shown} tanesi gösteriliyor. Hedef bilgisi ekleyerek "
        "veya toleransı daraltarak sonucu daraltın.",
        "{total} candidates were found; the best {shown} are shown. Add destination details "
        "or narrow the tolerance.",
    ),
    "randomized_mac": (
        "Cihaz rastgele (yerel yönetilen) bir MAC adresi kullanıyor; aynı kişi farklı "
        "ağlarda veya dönemlerde farklı MAC adresleri kullanabilir.",
        "The device uses a randomised (locally administered) MAC address; the same person may "
        "use different MAC addresses on other networks or at other times.",
    ),
    "arp_only": (
        "Bu IP-MAC eşleşmesi yalnızca saatlik ARP-LOG kayıtlarına dayanıyor (orta güven).",
        "This IP-MAC pairing rests only on the hourly ARP-LOG records (medium confidence).",
    ),
    "no_person": (
        "Cihaz bir kişiye bağlanmamış; departman VLAN'dan türetildi.",
        "The device is not linked to a person; the department is derived from the VLAN.",
    ),
    "infrastructure_device": (
        "Bu MAC adresi bir altyapı cihazına (yönlendirici, anahtar, güvenlik duvarı) ait.",
        "This MAC address belongs to an infrastructure device (router, switch, firewall).",
    ),
    "mac_multiple_ips": (
        "{mac} bu anda birden fazla IP adresi kullanıyordu: {ips}.",
        "{mac} held more than one IP address at that moment: {ips}.",
    ),
    "mac_not_at_ip": (
        "{mac} bu anda {ip} adresini kullanmıyordu; zincir {ip} adresinin o anki sahibini "
        "gösterir.",
        "{mac} did not hold {ip} at that moment; the chain shows who held {ip} then.",
    ),
    "no_lease_for_mac": (
        "{mac} için {at} anında geçerli bir DHCP/ARP kaydı yok.",
        "No DHCP/ARP lease of {mac} covers {at}.",
    ),
    "lease_timeline_behind": (
        "IP-MAC zaman çizelgesi {watermark} anına kadar hesaplandı; daha yeni anlar için "
        "ARCILOGGER worker hizmetinin çalıştığını kontrol edin.",
        "The IP-MAC timeline is computed up to {watermark}; for later moments check that the "
        "ARCILOGGER worker service is running.",
    ),
    "not_in_database": (
        "{day} gününe ait kayıt veritabanında yok. Kayıtlar mühürlü arşivde olabilir; "
        "arşivden yeniden yükleme sonraki bir sürümde gelecektir.",
        "There are no records of {day} in the database. They may be in the sealed vault; "
        "reloading from the vault comes in a later version.",
    ),
    "no_data_for_day": (
        "{day} günü için ne veritabanında ne de mühürlü arşivde kayıt bulundu.",
        "No records of {day} were found, neither in the database nor in the sealed vault.",
    ),
    "older_than_db_retention": (
        "{day} günü veritabanı saklama süresinden ({days} gün) eski; kayıtlar yalnızca "
        "mühürlü arşivde tutulur.",
        "{day} is older than the database retention ({days} days); the records are kept only "
        "in the sealed vault.",
    ),
    "future_time": (
        "İstenen an ({at}) henüz gelmedi veya sunucu saatinin ilerisinde.",
        "The requested moment ({at}) is in the future of the server clock.",
    ),
    "destination_search": (
        "Genel IP verilmedi: arama, zaman penceresinde istenen hedefe bağlanan oturumlarla "
        "sınırlıdır.",
        "No public IP given: the search is limited to sessions to the requested destination in "
        "the window.",
    ),
}


def _display(value: str | int | None) -> str:
    """Times and days in the Turkish display format, everything else as given."""
    if value is None:
        return "—"
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return format_date(date.fromisoformat(value))
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}T", value):
        try:
            return format_datetime(datetime.fromisoformat(value))
        except ValueError:
            return value
    return str(value)


def explain(key: str, **params: str | int | None) -> Explanation:
    """A standard or engine explanation with its placeholders filled."""
    if key in EXPLANATIONS:
        return Explanation.standard(key, **params)
    text_tr, text_en = ENGINE_EXPLANATIONS[key]
    shown = {name: _display(value) for name, value in params.items()}
    return Explanation(
        key=key, text_tr=text_tr.format(**shown), text_en=text_en.format(**shown), params=params
    )
