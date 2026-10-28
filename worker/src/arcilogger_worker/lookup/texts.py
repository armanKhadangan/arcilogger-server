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
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Final

from arcilogger_core.fmt import format_date, format_datetime
from arcilogger_db.egress import Egress
from arcilogger_worker.lookup.types import EXPLANATIONS, EgressChange, EgressEntry, Explanation

ENGINE_EXPLANATIONS: Final[Mapping[str, tuple[str, str]]] = {
    "no_lease": (
        "{ip} adresi için {at} anında geçerli bir kira kaydı yok. En yakın kayıtlar: önce "
        "{before}, sonra {after}. Kira kanıtı DHCP sunucusunun kayıtlarından (MikroTik DHCP ya "
        "da FortiGate DHCP olayları) ve MikroTik ARP-LOG kayıtlarından gelir: DHCP sunucusunun "
        'kayıt gönderdiğini Kaynaklar sayfasındaki "Kira kanıtı" kutusunda kontrol edin. Statik '
        "IP'li cihazlar yalnızca ARP-LOG ile (ya da srcmac ile kısmi kanıt olarak) görünür.",
        "No lease covers {ip} at {at}. Nearest leases: before {before}, after {after}. Lease "
        "evidence comes from the DHCP server's records (MikroTik DHCP or FortiGate DHCP events) "
        'and from MikroTik ARP-LOG lines: check on the Sources page ("Lease evidence") that the '
        "DHCP server sends them. Static-IP devices only appear through ARP-LOG (or, as partial "
        "evidence, through srcmac).",
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
        "{egresses} çıkışında servis sağlayıcının kaynak portu koruduğu sahada doğrulanmış olarak "
        "işaretli: FortiGate NAT portu istenen genel porta eşit adaylar öne alındı. Bu yalnızca "
        "sıralamadır; kesin eşleşme değildir.",
        "Exit(s) {egresses} are marked as verified on site to keep source ports at the ISP router: "
        "candidates whose FortiGate NAT port equals the requested public port are ranked first. "
        "This is only a ranking, not an exact match.",
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
        "NAT) adresi olarak görünmüyor ya da yalnızca bir kısmında görünüyor; kayıtlarda başka "
        "bir çıkış adresi de var. ISS modemi bu tarihte hâlâ NAT yapıyor olabilir (çift NAT) "
        "veya Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet "
        "çıkışları (NAT) değeri bu tarih için doğru değil. Bu nedenle kesin eşleşme yerine aday "
        "araması yapıldı.",
        "The requested address {address} does not appear, or only part of the time, as the "
        "FortiGate's source-NAT address in its logs around this moment; the logs also show "
        "another address. The ISP router may still have translated at that date (double NAT), or "
        "the value in Settings → Application settings → 5651 lookup and lease timeline → "
        "Internet exits (NAT) does not apply to that date. A candidate search was therefore made "
        "instead of an exact match.",
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
        "{mac} için {at} anında geçerli bir kira kaydı (DHCP ya da ARP-LOG) yok.",
        "No lease (DHCP or ARP-LOG) of {mac} covers {at}.",
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
    # ADR-0016: FortiGate DHCP evidence, srcmac and the internet exits.
    "chain_missing": (
        "{count} eşleşmede iç IP adresinden cihaza zincir kurulamadı: o anda bu adresler için kira "
        "kaydı yok. Bu eşleşmeler FortiGate oturumunda ve iç IP adresinde biter.",
        "For {count} match(es) no chain from the internal IP to a device could be built: no lease "
        "covered those addresses at that moment. These matches end at the FortiGate session and "
        "the internal IP address.",
    ),
    "fortigate_dhcp_not_converted": (
        "{ips} için o anı kapsayan FortiGate DHCP kayıtları var, ancak kira kanıtına "
        "çevrilmediler, bu nedenle zincir kurulmadı. Olası nedenler: kayıtlar bu sürüme "
        "yükseltmeden önce yüklendi; ip ya da mac alanları okunamadı (ayrıştırma hatası "
        "fortigate.dhcp, uyarı R20-lease:fortigate-unconverted); ya da kayıtları bir geri almadan "
        "sonra önceki sürüm yükledi. İç IP sorgusunda ve kesin eşleşmede kayıt, kanıt bölümünde "
        '"kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez" notuyla '
        "gösterilir (aday eşleşmelerde gösterilmez); bir eşleştirme değildir. Ham kayıtlar "
        "saklanır ve gece mühürlenir; Arama'da type:event ile görülebilir.",
        "There are FortiGate DHCP records for {ips} that cover that moment, but they did not "
        "become lease evidence, so no chain was built. Possible reasons: they were loaded before "
        "the upgrade to this version; their ip or mac fields could not be read (parse failure "
        "fortigate.dhcp, alert R20-lease:fortigate-unconverted); or the previous version loaded "
        "them after a rollback. In an internal-IP lookup and for an exact match the record is "
        'shown in the evidence section marked "not in the lease timeline; no chain built; no '
        'confidence given" (not for candidate matches); it is not an attribution. The raw lines '
        "are kept and sealed every night; they can be seen in Search with type:event.",
    ),
    "srcmac_only": (
        "Bu zincirde kira kaydı yok. MAC adresi FortiGate oturum kaydının srcmac alanından alındı "
        "({interface} arayüzü): FortiGate'in aygıt tanıma bilgisine göre kaynak IP adresi o anda "
        "bu MAC adresiyle ilişkilendirilmişti. Bu kısmi bir kanıttır; MAC adresinin bu oturumu "
        "gönderdiğini ya da adresin bir DHCP kirasıyla verildiğini kanıtlamaz. Cihaz ve kişi bu "
        "MAC adresine göre düşük güvenle gösterilir.",
        "This chain has no lease. The MAC address comes from the srcmac field of the FortiGate "
        "session record (interface {interface}): the FortiGate's device identification associated "
        "the source IP with this MAC address at that moment. This is partial evidence; it does not "
        "prove that this MAC sent the session or that a DHCP lease gave it the address. The device "
        "and the person are shown for this MAC address with low confidence.",
    ),
    "srcmac_agrees": (
        "FortiGate oturum kaydındaki srcmac ({mac}) kira kaydındaki MAC adresiyle aynı.",
        "The srcmac of the FortiGate session record ({mac}) is the MAC address of the lease.",
    ),
    "srcmac_mismatch": (
        "FortiGate oturum kaydındaki srcmac ({srcmac}) kira kaydındaki MAC adresinden ({mac}) "
        "farklı: o anda bu IP adresini iki cihaz kullanmış olabilir (IP çakışması, statik adresli "
        "bir cihaz ya da kayıtlarda görünmeyen bir adres çakışması). Zincir kira kaydına göre "
        "kuruldu, srcmac'e göre olası zincir de gösterildi; ikisi de düşük güvenlidir. Arayüz "
        "ayarını (lookup.srcmac_interfaces) da kontrol edin.",
        "The srcmac of the FortiGate session record ({srcmac}) differs from the lease's MAC "
        "address ({mac}): two devices may have used this IP address at that moment (an IP "
        "conflict, a device with a static address, or an address conflict that does not appear in "
        "the logs). The chain follows the lease and the possible chain by srcmac is shown too; "
        "both have low confidence. Also check the interface setting (lookup.srcmac_interfaces).",
    ),
    "srcmac_ambiguous": (
        "{ip} adresinden ±{seconds} saniyede {count} farklı srcmac görüldü (ilk üçü: {macs}); MAC "
        "adresi belirlenemedi.",
        "{count} different srcmac values were seen from {ip} within ±{seconds} s (the first three: "
        "{macs}); the MAC address could not be determined.",
    ),
    "srcmac_shared": (
        "srcmac {mac}, {at} anının ±1 saat içinde {count} farklı IP adresiyle görüldü: bir "
        "yönlendiricinin, anahtarın ya da ağ cihazının adresi olabilir; kullanılmadı.",
        "srcmac {mac} was seen with {count} distinct IP addresses within ±1 hour of {at}: it may "
        "belong to a router, switch or network device; it was not used.",
    ),
    "srcmac_other_lease": (
        "srcmac {mac}, {at} anının ±1 saat içinde başka bir IP adresinin ({other_ip}) kirasında "
        "görülüyor: bir yönlendiricinin ya da anahtarın adresi olabilir; kullanılmadı.",
        "srcmac {mac} holds a lease of another IP address ({other_ip}) within ±1 hour of {at}: it "
        "may belong to a router or switch; it was not used.",
    ),
    "srcmac_infrastructure": (
        "srcmac {mac} bir altyapı cihazının adresi; kullanılmadı.",
        "srcmac {mac} belongs to an infrastructure device; it was not used.",
    ),
    "egress_search": (
        "Arama {egress} çıkışıyla sınırlandı: bu çıkış servis sağlayıcı NAT'ı olarak tanımlı "
        "(ayarlara göre servis sağlayıcının yönlendiricisi bu hatta ikinci bir NAT yapıyor). "
        "FortiGate'in bu hattaki NAT adresleri: {transips}; arayüzler: {interfaces}.",
        "The search was limited to exit {egress}: this exit is defined as ISP NAT (by the "
        "settings, the ISP router does a second NAT on this line). The FortiGate's NAT addresses "
        "on this line: {transips}; interfaces: {interfaces}.",
    ),
    "egress_public_unknown": (
        "{address} hiçbir çıkışın genel adresi olarak tanımlı değil; genel adresi tanımlanmamış ya "
        "da değişebilen çıkışlarda arandı: {egresses}. Adres tanımlı bir hatta ait değilse liste "
        "yanlış hattın kullanıcılarını gösterebilir; adresin ait olduğu hattın genel adresini "
        "Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları "
        "(NAT) bölümünde ekleyin.",
        "{address} is not the public address of any exit; it was searched in the exits whose "
        "public address is not defined or may change: {egresses}. If the address does not belong "
        "to such a line, the list may show the users of the wrong line; add the public address of "
        "the line it belongs to in Settings → Application settings → 5651 lookup and lease "
        "timeline → Internet exits (NAT).",
    ),
    "public_ip_no_egress": (
        "{address} hiçbir internet çıkışına tanımlı değil; kimse listelenmedi. Adres kurumun bir "
        "hattına aitse Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet "
        "çıkışları (NAT) bölümünde tanımlayıp sorguyu yeniden çalıştırın.",
        "{address} belongs to no internet exit; nobody was listed. If it is one of the "
        "organisation's lines, define it in Settings → Application settings → 5651 lookup and "
        "lease timeline → Internet exits (NAT) and run the lookup again.",
    ),
    "egress_not_logged": (
        "{address}, {egress} çıkışına ait; bu çıkış FortiGate üzerinden geçmiyor. FortiGate'te bu "
        "hattın oturum kaydı yok ve kimse listelenmedi; bu hattın kaydı ancak hattı yöneten "
        "cihazdan alınabilir.",
        "{address} belongs to exit {egress}, which does not pass the FortiGate. The FortiGate has "
        "no session records of this line and nobody was listed; records of this line can only come "
        "from the device that runs it.",
    ),
    "egress_transip_not_seen": (
        "{egress} çıkışının NAT adresleri ({transips}) {at} çevresinde (±15 dakika) kayıtlarda "
        "görülmedi; arama hiçbir çıkışa tanımlı olmayan bütün özel ya da paylaşılan kaynak NAT "
        "adreslerine genişletildi ({at} çevresinde görülenler: {seen}); arayüz koşulu: "
        "{interfaces}.",
        "The NAT addresses of exit {egress} ({transips}) do not appear in the logs around {at} "
        "(±15 minutes); the search was widened to every private or shared source-NAT address that "
        "no exit claims (seen around {at}: {seen}); interface condition: {interfaces}.",
    ),
    "egress_interface_not_seen": (
        "{at} çevresinde {egress} çıkışının adresleri kayıtlarda yalnızca bu çıkışın arayüzleri "
        "({interfaces}) dışındaki arayüzlerde görünüyor: arayüz adları değişmiş ya da yanlış "
        "girilmiş olabilir; arama arayüz koşulu olmadan yapıldı.",
        "Around {at} the logs show the addresses of exit {egress} only on interfaces other than "
        "its own ({interfaces}): the interface names may have changed or been entered wrongly; the "
        "search was made without the interface condition.",
    ),
    "egress_interface_not_seen_kept": (
        "{at} çevresinde {egress} çıkışının NAT adresi kayıtlarda bu çıkışın arayüzlerinde "
        "({interfaces}) görünmüyor; bu NAT adresi başka bir çıkışla paylaşıldığı için arama arayüz "
        "koşuluyla sınırlı kaldı ve sonuç boş olabilir.",
        "Around {at} the logs do not show the NAT address of exit {egress} on its interfaces "
        "({interfaces}); because the address is shared with another exit the search kept the "
        "interface condition and may find nothing.",
    ),
    "egress_unmapped_seen": (
        "{at} çevresinde hiçbir çıkışa tanımlı olmayan kaynak NAT adresleri de görüldü: "
        "{addresses}. Çıkış tanımları eksik olabilir (Ayarlar → Uygulama ayarları → 5651 sorgu ve "
        "kira zaman çizelgesi → İnternet çıkışları (NAT)).",
        "Around {at} the logs also show source-NAT addresses that no exit claims: {addresses}. The "
        "exit definitions may be incomplete (Settings → Application settings → 5651 lookup and "
        "lease timeline → Internet exits (NAT)).",
    ),
    "egress_unconfigured": (
        "İnternet çıkışları tanımlanmamış: servis sağlayıcının yönlendiricisinin ikinci bir NAT "
        "yaptığı varsayıldı ve özel ya da paylaşılan bir NAT adresiyle (RFC 1918, RFC 6598) çıkan "
        "bütün kaynak NAT oturumları arandı. Genel bir NAT adresiyle çıkan oturumlar FortiGate'in "
        "kendi adresinden çıktığı için aranmadı; o adres sorulursa kayıtlarda görülen adres olarak "
        "yanıtlanır. Hatlarınızı Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi "
        "→ İnternet çıkışları (NAT) bölümünde tanımlayın.",
        "No internet exits are defined: a second NAT by the ISP router was assumed and every "
        "source-NAT session with a private or shared NAT address (RFC 1918, RFC 6598) was "
        "searched. Sessions that left with a public NAT address left through the FortiGate's own "
        "address and were not searched; that address, when asked about, is answered as an address "
        "seen in the logs. Define your lines in Settings → Application settings → 5651 lookup and "
        "lease timeline → Internet exits (NAT).",
    ),
    "nat_unknown_trandisp": (
        "Bu aralıkta {count} oturum ayarlarda tanımlı olmayan bir NAT türüyle ({values}) "
        "kaydedildi ve aday aramasına alınmadı. nat.snat_trandisp ayarını sahada doğrulayın.",
        "In this window {count} sessions were logged with a NAT type that is not in the settings "
        "({values}) and were not searched. Verify the nat.snat_trandisp setting on site.",
    ),
    "fortigate_format_unverified": (
        "FortiGate DHCP kayıt biçimi bu kurulumda henüz sahada doğrulanmadı (DEVICE-SETUP.md bölüm "
        "2.4); kira kaydı FortiOS belgelerindeki biçime göre okundu.",
        "The FortiGate DHCP record format has not yet been verified on site at this installation "
        "(DEVICE-SETUP.md section 2.4); the lease was read by the format of the FortiOS "
        "documentation.",
    ),
    "lease_renewal_not_seen": (
        "Bu kiranın istenen andan önceki son DHCP kaydı {last} anında (kira süresi {lease} sn). "
        "İstenen an bundan kira süresinin yarısından fazla sonra ve arada bir yenileme kaydı yok. "
        "Adresi hâlâ tutan bir istemci kira süresinin yarısında yeniler; bu yenileme kayıtlarda "
        "yoksa ya yenileme kaydedilmedi ya da adresi o anda başka bir cihaz (ör. havuzda statik "
        "adresli bir cihaz) kullanıyordu. Kayıtlar hangisi olduğunu gösteremez; zincir bu nedenle "
        "düşük güvenle gösterildi.",
        "The last DHCP record of this lease before the requested moment is at {last} (lease time "
        "{lease} s). The requested moment is more than half the lease time later, with no renewal "
        "record in between. A client that still holds the address renews at half the lease; if "
        "that renewal is not in the logs, either it was not logged or another device (e.g. a "
        "static device in the pool) used the address at that moment. The records cannot show "
        "which, so the chain is shown with low confidence.",
    ),
    "fortigate_dhcp_not_converted_since_lease": (
        "Bu kiranın {last} anındaki son DHCP kaydından sonra, istenen andan önce, {ip} adresi ya "
        "da bu kiranın MAC adresi için kira zaman çizelgesinin bu kiraya uygulamadığı FortiGate "
        "DHCP kayıtları var (ör. bu sürüme yükseltmeden önce yüklendi, bir geri almadan sonra "
        "önceki sürüm yükledi, alanları okunamadı ya da kiralar bir ağ tanımlanmadan veya "
        "değiştirilmeden önce oluşturuldu). Bu kayıtlar adresin bırakıldığını, başka bir cihaza "
        "verildiğini ya da bu MAC adresinin başka bir adres aldığını gösteriyor olabilir; zincir "
        "bu nedenle düşük güvenle gösterildi. Kayıtlar başka bir istemciye ait olabileceği için "
        "gösterilmez; ham kayıtlar saklanır ve gece mühürlenir (Arama'da type:event). Ağlar "
        "(Cihazlar ve Kişiler → Departmanlar ve VLAN'lar) sonradan tanımlandı ya da "
        "değiştirildiyse kiraları bu kiranın başladığı günden bugüne Ayarlar → Uygulama ayarları → "
        "5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun "
        "(bir istekte en çok 31 gün).",
        "After this lease's last DHCP record at {last} and before the requested moment there are "
        "FortiGate DHCP records for {ip} or for this lease's MAC address that the lease timeline "
        "did not apply to this lease (e.g. loaded before the upgrade to this version, loaded by "
        "the previous version after a rollback, fields that could not be read, or leases built "
        "before a network was entered or changed). They may show that the address was released or "
        "given to another device, or that this MAC address took another address, so the chain is "
        "shown with low confidence. The records are not shown, because they may belong to another "
        "client; the raw lines are kept and sealed every night (Search, type:event). If networks "
        "(Devices and people → Departments and VLANs) were entered or changed later, rebuild the "
        "leases from this lease's start day to today in Settings → Application settings → 5651 "
        "lookup and lease timeline → Rebuild the leases (at most 31 days per request).",
    ),
    "fortigate_dhcp_no_network": (
        "{ip} adresi tanımlı hiçbir ağda değil (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar). "
        "Bir istemcinin FortiGate'ten aldığı adresi reddedip (DHCPDECLINE, ör. adresi havuzda "
        "statik adresli bir cihaz kullanıyorsa) başka bir adres aldığı yalnızca tanımlı ağlarda "
        "anlaşılır; bu kira o anda adresi kullanan cihazı göstermiyor olabilir, zincir bu nedenle "
        "düşük güvenle gösterildi. Ağı VLAN numarası olmasa da tanımlayın; sonra kiraları bu "
        "kiranın başladığı günden bugüne Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman "
        "çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun (bir istekte en çok 31 "
        "gün): bir ağ geçmiş kiralara ancak yeniden oluşturmadan sonra uygulanır.",
        "{ip} lies in no configured network (Devices and people → Departments and VLANs). A client "
        "that declines an address the FortiGate gave it (DHCPDECLINE, e.g. because a static device "
        "in the pool uses it) and takes another is only detected in configured networks; this "
        "lease may not show the device that used the address at that moment, so the chain is shown "
        "with low confidence. Define the network, even without a VLAN number, then rebuild the "
        "leases from this lease's start day to today in Settings → Application settings → 5651 "
        "lookup and lease timeline → Rebuild the leases (at most 31 days per request): a "
        "network applies to past leases only after a rebuild.",
    ),
    "egress_observed_not_configured": (
        "{address} hiçbir internet çıkışında tanımlı değil, ancak {at} çevresinde (±15 dakika) "
        "FortiGate kayıtlarında kaynak NAT adresi olarak görünüyor: FortiGate bu adresi o sırada "
        "kendisi kullanıyordu (ör. değişken genel IP'li bir hat). Eşleşme FortiGate kaydında "
        "yapıldı. Adresin o tarihte kuruma ait olduğunu servis sağlayıcıdan doğrulayın ve hattı "
        "Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları "
        "(NAT) bölümünde tanımlayın.",
        "{address} is not defined in any internet exit, but around {at} (±15 minutes) the "
        "FortiGate logs show it as a source-NAT address: the FortiGate itself used this address at "
        "that time (e.g. a line with a changing public IP). The match was made on the FortiGate "
        "record. Confirm with the ISP that the address belonged to the organisation on that date, "
        "and define the line in Settings → Application settings → 5651 lookup and lease timeline → "
        "Internet exits (NAT).",
    ),
    "egress_changed_after_event": (
        "{at} anından sonra şu internet çıkışlarının tanımı değişti: {changes_tr}. Sorgu bugünkü "
        "tanımlarla yapıldı; bu çıkışların o tarihteki adresleri farklıysa sonuç farklı olabilir. "
        "Önceki tanımlar kurumun denetim kaydındadır (settings.change) ve sistem yöneticisinden "
        "istenebilir.",
        "These internet exits were changed after {at}: {changes_en}. The lookup used today's "
        "definitions; if these exits had other addresses on that date, the result may differ. The "
        "earlier definitions are in the organisation's audit log (settings.change) and can be "
        "requested from its administrator.",
    ),
    "session_changed_exit": (
        "{count} oturumun kayıtları farklı çıkışlarda görünüyor ({egresses}); her eşleşmenin "
        "çıkışı istenen ana en yakın kayda göre verildi.",
        "The records of {count} session(s) appear on different exits ({egresses}); each match's "
        "exit is taken from its record nearest to the requested moment.",
    ),
    "egress_not_logged_but_seen": (
        "{address}, {egress} çıkışına FortiGate'ten geçmeyen bir hat olarak tanımlı, ancak {at} "
        "çevresinde (±15 dakika) FortiGate kayıtlarında kaynak NAT adresi olarak görünüyor: hat "
        "FortiGate'e taşınmış ya da türü yanlış girilmiş olabilir. Eşleşme FortiGate kaydında "
        "yapıldı. Çıkışın türünü Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi "
        "→ İnternet çıkışları (NAT) bölümünde düzeltin.",
        "{address} is defined in exit {egress} as a line that does not pass the FortiGate, but "
        "around {at} (±15 minutes) the FortiGate logs show it as a source-NAT address: the line "
        "may have been moved behind the FortiGate, or its type entered wrongly. The match was made "
        "on the FortiGate record. Correct the exit's type in Settings → Application settings → "
        "5651 lookup and lease timeline → Internet exits (NAT).",
    ),
    "dhcp_sources_overlap": (
        "{network} alt ağında {at} çevresinde iki DHCP kaynağından kayıt var ({sources}): aynı "
        "ağda iki DHCP sunucusu olabilir. Zincir düşük güvenle gösterildi (uyarı "
        "R20-lease:dhcp-sources-overlap).",
        "Around {at} the subnet of {network} has records of two DHCP sources ({sources}): there "
        "may be two DHCP servers on one network. The chain is shown with low confidence (alert "
        "R20-lease:dhcp-sources-overlap).",
    ),
    "fortigate_format_verified": (
        "FortiGate DHCP kayıt biçimi bu kurulumda {at} tarihinde {by} tarafından sahada doğrulandı "
        "olarak işaretlendi (DEVICE-SETUP.md bölüm 2.4).",
        "The FortiGate DHCP record format was marked as verified on site at this installation on "
        "{at} by {by} (DEVICE-SETUP.md section 2.4).",
    ),
    "session_from_unknown_source": (
        "{count} eşleşmenin kayıtları yapılandırmada tanımlı olmayan bir göndericiden geldi "
        "({sources}); bu eşleşmeler ayrıca kendi satırlarında işaretlidir. Bu kayıtlar kurumun "
        "FortiGate'inden geldiği doğrulanmadan kanıt olarak kullanılmamalıdır (uyarı \"yeni log "
        'kaynağı").',
        "The records of {count} match(es) came from a sender that is not configured ({sources}); "
        "those matches are also marked on their own rows. Do not use them as evidence until it is "
        'confirmed that they came from the organisation\'s FortiGate (alert "new log source").',
    ),
    "source_configured_later": (
        "Bu sonuçtaki kayıtların bir kısmı {sources} kaynağından: gönderici, kayıtlar alındığında "
        "yapılandırmada tanımlı değildi ve adresi sonradan bir FortiGate kaynağı olarak tanımlandı "
        "(gönderici sonradan yapılandırıldı). Kayıtlar bu yüzden kanıt sayıldı.",
        "Some records of this result come from {sources}: the sender was not configured when they "
        "arrived, and its address was configured as a FortiGate source later (sender configured "
        "later). The records were therefore used as evidence.",
    ),
    "egress_public_dynamic": (
        "{egress} hattının genel adresi değişebilir olarak işaretli; {address} adresinin {at} "
        "anında bu hatta ait olduğu yalnızca servis sağlayıcıdan doğrulanabilir.",
        "The public address of {egress} is marked as changing; that {address} belonged to this "
        "line at {at} can only be confirmed by the ISP.",
    ),
    "egress_internal": (
        "{address}, {egress} tanımına göre FortiGate'in iç ağlara (DMZ, başka bir VLAN, VPN ya da "
        "şube) yaptığı kaynak NAT'ın adresidir, bir internet çıkışı değildir; oturum aranmadı ve "
        "kimse listelenmedi. Adres bir internet hattına aitse tanımı Ayarlar → Uygulama ayarları → "
        "5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde düzeltin.",
        "{address} is, by the definition {egress}, the address of the FortiGate's source NAT "
        "towards internal networks (a DMZ, another VLAN, a VPN or a branch), not an internet exit; "
        "no session was searched and nobody was listed. If the address belongs to an internet "
        "line, correct the definition in Settings → Application settings → 5651 lookup and lease "
        "timeline → Internet exits (NAT).",
    ),
    "source_clock_offset": (
        "{at} çevresinde şu kaynakların kayıt zamanları ARCILOGGER'ın alma zamanından ortanca "
        "olarak saniye cinsinden şu kadar farklıydı: {offsets} (sınır {limit} saniye). Arama ve "
        "kira zaman çizelgesi cihazın zamanlarını kullandı: doğru oturum ya da kira bu fark kadar "
        "kaymış bir zaman aralığında olabilir; doğru oturum bulunmamış ve başka bir oturum "
        "bulunmuş olabilir, kira da o anı kapsamıyor olabilir. Bu sonuçta zincir varsa düşük "
        "güvenle gösterildi; sonuç boşsa toleransı bu fark kadar genişletip sorguyu yeniden "
        "çalıştırın.",
        "Around {at} the record times of these sources differed from ARCILOGGER's receive time by "
        "a median of (in seconds): {offsets} (limit {limit} seconds). The search and the lease "
        "timeline used the device's times: the right session or lease may lie in a window shifted "
        "by that offset; the right session may have been missed and another found, and the lease "
        "may not cover the moment. Any chain of this result is therefore shown with low "
        "confidence; if the result is empty, widen the tolerance by the offset and run the lookup "
        "again.",
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


#: Internal authors of a settings row, in words (D32: never the raw ``:migration``).
_AUTHORS: Final[Mapping[str, tuple[str, str]]] = {
    ":migration": (
        "yükseltme (eski NAT ayarlarından aktarıldı)",
        "upgrade (converted from the old NAT settings)",
    ),
    ":seed": ("varsayılan değer", "shipped default"),
    ":system": ("varsayılan değer", "shipped default"),
}


def exit_changes(changes: Sequence[EgressChange], exits: Sequence[Egress]) -> tuple[str, str]:
    """The changed exits of ``egress_changed_after_event`` in Turkish and in English (one
    placeholder each, ``{changes_tr}``/``{changes_en}``): name, time and author of the change,
    and what the exit holds now — "hat-2 (20.10.2026 08:12:00, yonetici; şimdi: genel
    198.51.100.77, NAT 100.64.13.9)", or "silindi" / "deleted"."""
    now = {egress.name: EgressEntry.from_egress(egress) for egress in exits}
    turkish, english = [], []
    for change in changes:
        when = format_datetime(change.changed_at)
        by_tr, by_en = _AUTHORS.get(change.changed_by, (change.changed_by, change.changed_by))
        entry = now.get(change.name)
        if change.deleted or entry is None:
            held_tr, held_en = "silindi", "deleted"
        else:
            public = ", ".join(entry.public) or "-"
            nat = ", ".join(entry.transips) or "-"
            held_tr = f"şimdi: genel {public}, NAT {nat}"
            held_en = f"now: public {public}, NAT {nat}"
        turkish.append(f"{change.name} ({when}, {by_tr}; {held_tr})")
        english.append(f"{change.name} ({when}, {by_en}; {held_en})")
    return "; ".join(turkish), "; ".join(english)
