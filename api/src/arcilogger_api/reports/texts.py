"""Fixed texts of the official 5651 report (Turkish first, English second).

Neutral wording only: the report never claims legal compliance (spec 0.6, 21) and says where a
statement rests on an administrative assignment instead of log evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

TITLE_TR: Final = "5651 Erişim Kaydı Sorgu Raporu"
TITLE_EN: Final = "Official report — 5651 access record lookup"

LEGAL_NOTE_TR: Final = (
    "ARCILOGGER, 5651 sayılı Kanun ve İnternet Toplu Kullanım Sağlayıcıları Hakkında Yönetmelik "
    "kapsamındaki yükümlülükleri desteklemek için tasarlanmıştır. Bu rapor bir hukuki uygunluk "
    "beyanı değildir; raporun ve dayandığı kayıtların hukuki değerlendirmesi için bir hukuk "
    "danışmanına başvurulması önerilir."
)
LEGAL_NOTE_EN: Final = (
    "ARCILOGGER is designed to support the obligations of Turkish Law 5651; this report does not "
    "claim legal compliance. Have the report and its records assessed by a lawyer."
)
FOOTER_NOTE_TR: Final = (
    "ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır; bu rapor hukuki uygunluk "
    "beyanı değildir."
)
FOOTER_NOTE_EN: Final = (
    "Designed to support 5651 obligations; this report is not a statement of legal compliance."
)
PERSON_NOTE_TR: Final = (
    "Cihazın bir kişiye bağlanması ARCILOGGER'da yapılan idari bir atamadır, log kanıtı değildir; "
    'zincirin kişi adımı bu nedenle en fazla "orta" güvendedir.'
)
PERSON_NOTE_EN: Final = (
    "Linking a device to a person is an administrative assignment made in ARCILOGGER, not log "
    'evidence; the person step of a chain is therefore at most "medium" confidence.'
)

METHOD_TR: Final = (
    "ARCILOGGER, ağ cihazlarından gelen her syslog mesajını alındığı anda değiştirmeden ham kayıt "
    "dosyasına yazar ve aynı satırı veritabanına ayrıştırır. Sorgu iki adımda yanıtlanır: "
    "(1) FortiGate oturum kayıtlarındaki kaynak NAT bilgisi (iç IP:port → transip:transport) ile "
    "istenen zamandaki oturum bulunur; (2) DHCP sunucusunun kayıtlarından (MikroTik DHCP, "
    "FortiGate DHCP olayları) ve saatlik ARP-LOG kayıtlarından kurulan IP\N{EN DASH}MAC zaman "
    "çizelgesiyle iç IP adresinin o anda hangi MAC adresinde olduğu, cihaz kaydı ve (varsa) "
    "cihazın bağlı olduğu kişi ve departman belirlenir. Her adım, dayandığı veritabanı satırını "
    "ve ham kayıt satırını (kanıt listesi, bölüm 6) gösterir."
)
METHOD_EN: Final = (
    "ARCILOGGER writes every syslog message unchanged to a raw log file the moment it arrives and "
    "parses the same line into the database. A lookup has two steps: (1) the source NAT of the "
    "FortiGate session logs (internal IP:port → transip:transport) finds the session at the "
    "requested time; (2) the IP\N{EN DASH}MAC timeline built from the DHCP server's records "
    "(MikroTik DHCP, FortiGate DHCP events) and hourly ARP-LOG lines gives the MAC address that "
    "held the internal IP at that moment, the device record and, if linked, the person and "
    "department. Every step cites its database row and raw log line (evidence list, section 6)."
)
#: Double NAT (spec 12.4, contract 2.12): why the answer cannot be exact (an exit configured as
#: ISP NAT, or a second NAT assumed) and the CGNAT caveat, then, by mode, what the result is,
#: then what would make the answer exact. The "candidate list" sentence appears only when
#: candidates follow (docs/MVP-CONTRACTS.md section 5: ``double_nat`` vs
#: ``double_nat_no_candidates``). A ``fallback`` prints neither variant nor the CGNAT sentence
#: (its method paragraph stands in for them) and only advice (a), with the bridge-mode check
#: for a failed ``direct`` exit only.
DOUBLE_NAT_CONFIGURED_TR: Final = (
    "Çift NAT: {exit} çıkışı servis sağlayıcı NAT'ı olarak tanımlı; servis sağlayıcının "
    "yönlendiricisi FortiGate'in adresini ikinci kez çevirir. ARCILOGGER'da bu ikinci dönüşümün "
    "kaydı yoktur; bu nedenle talepteki genel port FortiGate'in çıkış portuna (transport) kesin "
    "olarak eşlenemez."
)
DOUBLE_NAT_CONFIGURED_EN: Final = (
    "Double NAT: exit {exit} is defined as ISP NAT; the ISP router translates the FortiGate's "
    "address a second time. ARCILOGGER has no record of that second translation, so the public "
    "port of the request cannot be mapped exactly to the FortiGate's outgoing port (transport)."
)
DOUBLE_NAT_ASSUMED_TR: Final = (
    "Çift NAT varsayımı: bu adres için servis sağlayıcının ikinci bir NAT yaptığı varsayıldı; "
    "ARCILOGGER'da böyle bir dönüşümün kaydı yoktur ve talepteki genel port FortiGate'in çıkış "
    "portuna kesin olarak eşlenemez."
)
DOUBLE_NAT_ASSUMED_EN: Final = (
    "Double NAT assumed: a second NAT by the ISP was assumed for this address; ARCILOGGER has no "
    "record of such a translation, and the public port of the request cannot be mapped exactly "
    "to the FortiGate's outgoing port."
)
DOUBLE_NAT_CGNAT_TR: Final = (
    "Genel adres başka müşterilerle paylaşılıyorsa (CGNAT) aday listesi doğru oturumu "
    "içermeyebilir; kesin yanıt için servis sağlayıcının NAT kaydı gerekir."
)
DOUBLE_NAT_CGNAT_EN: Final = (
    "If the public address is shared with other customers (CGNAT), the candidate list may not "
    "contain the right session; an exact answer needs the ISP's NAT record."
)
DOUBLE_NAT_RESULT_TR: Final[Mapping[str, str]] = {
    "candidates": (
        "Sonuç, zaman penceresi (± tolerans) içindeki oturumların zamana ve hedefe göre "
        "sıralanmış aday listesidir."
    ),
    "none": (
        "Bu sorguda zaman penceresi (± tolerans) içinde ölçütlere uyan aday oturum da "
        "bulunamadı; bu nedenle rapor bir sonuç içermez."
    ),
}
DOUBLE_NAT_RESULT_EN: Final[Mapping[str, str]] = {
    "candidates": (
        "The result is a list of candidate sessions in the time window (± tolerance), ranked by "
        "time and destination."
    ),
    "none": (
        "No candidate session matching the criteria was found in the time window (± tolerance) "
        "either, so the report contains no result."
    ),
}
DOUBLE_NAT_ADVICE_TR: Final = (
    "Kesin eşleşme için şunlardan biri gerekir: (a) servis sağlayıcının ilgili zamana ait NAT "
    "kaydı (genel port → FortiGate çıkış portu); (b) servis sağlayıcı yönlendiricisinin köprü "
    "(bridge) moduna alınması ve FortiGate'in genel IP adresini doğrudan tutması (Ayarlar → "
    "Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde "
    "bu hattı 'Doğrudan' olarak tanımlayın)."
)
DOUBLE_NAT_ADVICE_EN: Final = (
    "An exact answer needs either (a) the ISP's NAT record for that time (public port → "
    "FortiGate port) or (b) the ISP router in bridge mode with the public address on the "
    "FortiGate (define this line as 'Direct' in Settings → Application settings → 5651 lookup "
    "and lease timeline → Internet exits (NAT))."
)
#: The advice of a ``fallback``: (a) only; ``{bridge}`` is :data:`DOUBLE_NAT_BRIDGE` for a
#: failed ``direct`` exit, else empty (deviation d: the lead-in of a one-item advice).
DOUBLE_NAT_ADVICE_A_TR: Final = (
    "Kesin eşleşme için (a) servis sağlayıcının ilgili zamana ait NAT kaydı (genel port → "
    "FortiGate çıkış portu){bridge} gerekir."
)
DOUBLE_NAT_ADVICE_A_EN: Final = (
    "An exact answer needs (a) the ISP's NAT record for that time (public port → FortiGate "
    "port){bridge}."
)
DOUBLE_NAT_BRIDGE: Final = (
    " ya da hattın o tarihte köprü modunda olduğunun servis sağlayıcıdan doğrulanması",
    " or confirmation from the ISP that the line was in bridge mode on that date",
)

#: The internet exit of a public-IP lookup (contract 2.12): one TR/EN label per resolution,
#: shared with the web header line (2.13) and the reading table of docs/RAPOR.md (2.15); a
#: ``fallback`` is labelled by the mode of the exit whose check failed (``fallback_direct``, or
#: ``fallback_transip`` for an ``isp_nat`` exit's NAT address).
EXIT_LABELS: Final[Mapping[str, tuple[str, str]]] = {
    "public": ("Genel IP", "Public IP"),
    "transip": ("NAT adresi", "NAT address"),
    "fallback_direct": ("Doğrudan hat doğrulanamadı", "Direct line not confirmed"),
    "fallback_transip": ("NAT adresi doğrulanamadı", "NAT address not confirmed"),
    "observed": ("Kayıtlarda görülen adres", "Address seen in the logs"),
    "public_unknown": ("Genel adresi bilinmeyen hatlar", "Lines with unknown public address"),
    "unconfigured": ("Çıkış tanımlı değil", "No exits defined"),
    "no_egress": ("Çıkış yok", "No exit"),
}
#: The exit modes (``nat.egresses`` ``mode``) in the report.
EXIT_MODES: Final[Mapping[str, tuple[str, str]]] = {
    "direct": ("Doğrudan", "Direct"),
    "isp_nat": ("Servis sağlayıcı NAT'ı", "ISP NAT"),
    "not_logged": ("FortiGate'ten geçmiyor", "Does not pass the FortiGate"),
    "internal": ("İnternet çıkışı değil", "Not an internet exit"),
}
#: The explanation keys of the reading table (contract 2.15, in the order of its rows): each
#: has a row in docs/RAPOR.md and docs/DENETCI-KILAVUZU.md, named in backticks in its first
#: column (the docs test of 3.9 checks both directions).
READING_TABLE_KEYS: Final[tuple[str, ...]] = (
    "egress_public_dynamic",
    "srcmac_only",
    "srcmac_mismatch",
    "fortigate_format_unverified",
    "fortigate_format_verified",
    "fortigate_dhcp_not_converted",
    "lease_renewal_not_seen",
    "fortigate_dhcp_not_converted_since_lease",
    "fortigate_dhcp_no_network",
    "dhcp_sources_overlap",
    "source_configured_later",
    "source_clock_offset",
    "egress_internal",
    "session_from_unknown_source",
    "egress_changed_after_event",
)

#: Yöntem / Method: one paragraph per (resolution, mode) of the exit (contract 2.12). ``{exit}``
#: is the exit's "name (label)", ``{exits}`` / ``{other}`` the same, comma-joined, for the exits
#: searched / the other exits a fallback searched, ``{address}`` the requested address,
#: ``{seen}`` the private or shared addresses outside the exit's own that the logs showed
#: (``searched.outside_seen``), ``{searched}`` the addresses actually searched.
METHOD_PUBLIC_DIRECT_TR: Final = (
    "{exit} çıkışı doğrudan tanımlı: genel IP adresi {address} FortiGate'in kendisindedir ve "
    "oturum kayıtlarında kaynak NAT adresi (transip) olarak görünür. Talepteki genel port "
    "FortiGate'in NAT portuyla (transport) karşılaştırıldı (port yoksa o adresle çıkan oturumlar "
    "aday olarak sıralandı)."
)
METHOD_PUBLIC_DIRECT_EN: Final = (
    "Exit {exit} is defined as direct: the public IP address {address} is on the FortiGate "
    "itself and appears in its session records as the source-NAT address (transip). The "
    "requested public port was compared with the FortiGate's NAT port (transport) (without a "
    "port, the sessions that left through that address were ranked as candidates)."
)
METHOD_ISP_NAT_TR: Final = (
    "{exit} çıkışı servis sağlayıcı NAT'ı olarak tanımlı: bu tanıma göre servis sağlayıcının "
    "yönlendiricisi FortiGate'in bu hattaki NAT adresini ({transips}) ikinci kez çevirir ve bu "
    "dönüşümün kaydı ARCILOGGER'da yoktur."
)
METHOD_ISP_NAT_EN: Final = (
    "Exit {exit} is defined as ISP NAT: by this definition the ISP router translates the "
    "FortiGate's NAT address on this line ({transips}) a second time and ARCILOGGER has no "
    "record of that translation."
)
#: ``{transips}`` of a catch-all exit (an ``isp_nat`` exit without NAT addresses covers every NAT
#: address no exit claims; deviation d: the empty value in words).
CATCH_ALL_TRANSIPS: Final = (
    "hiçbir çıkışa tanımlı olmayan özel ya da paylaşılan NAT adresleri",
    "the private or shared NAT addresses no exit claims",
)
METHOD_ISP_NAT_LIMITED: Final = (
    "Arama bu çıkışın NAT adresleriyle{interfaces} sınırlandı.",
    "The search was limited to this exit's NAT addresses{interfaces}.",
)
METHOD_INTERFACES: Final = (" ve arayüzleriyle ({interfaces})", " and interfaces ({interfaces})")
#: In place of the "limited" sentence when the search covered the unclaimed addresses too (a
#: catch-all exit, or O5 widened it), and after the ``public_unknown`` paragraph then (deviation
#: d, round-5 note "isp_nat paragraph and egress_search under O5").
METHOD_SEARCHED: Final = (
    "Arama {searched} adresleriyle{interfaces} yapıldı.",
    "The search covered {searched}{interfaces}.",
)
METHOD_SEARCHED_INTERFACES: Final = (
    " ve arayüzleriyle ({interfaces})",
    " on the interfaces ({interfaces})",
)
#: ``{searched}`` when the search covered the unclaimed addresses: after the addresses searched
#: (joined with " ve " / " and "), or alone. ``{at}`` is the requested moment in the report's
#: time format, where 2.12 writes "T", as ``egress_transip_not_seen`` says it (deviation d).
UNCLAIMED_SEARCHED: Final = (
    "hiçbir çıkışa tanımlı olmayan bütün özel ya da paylaşılan kaynak NAT adresleri "
    "({at} çevresinde görülenler: {seen})",
    "every private or shared source-NAT address no exit claims (seen around {at}: {seen})",
)
METHOD_PORT: Final = ("Port koruma: {port}.", "Port preservation: {port}.")
PORT_USED: Final = (
    "yalnızca sıralamada kullanıldı (sahada doğrulandı olarak işaretli)",
    "used for ranking only (marked as verified on site)",
)
PORT_NOT_USED: Final = ("kullanılmadı", "not used")
METHOD_TRANSIP_TR: Final = (
    "İstenen adres bu çıkışta FortiGate'in kendi NAT adresidir; eşleşme FortiGate kaydında "
    "transip ve transport ile yapıldı."
)
METHOD_TRANSIP_EN: Final = (
    "The requested address is the FortiGate's own NAT address on this exit; the match was made "
    "on the FortiGate record by transip and transport."
)
#: ``{on_line}`` is :data:`ON_LINE` only when the exit declares interfaces, so that ``{seen}``
#: was seen on its own line (deviation d, round-7 note (c)); empty otherwise.
METHOD_FALLBACK_DIRECT_TR: Final = (
    "{exit} doğrudan olarak tanımlı, ancak {address} o anın çevresinde FortiGate kayıtlarında "
    "NAT adresi olarak görünmüyor ya da yalnızca bir kısmında görünüyor; kayıtlar o sırada"
    "{on_line} özel ya da paylaşılan başka kaynak NAT adresleri ({seen}) de gösteriyor. Bu "
    "nedenle kesin eşleşme yapılamadı ve {searched} adresleriyle çıkan oturumlar aday olarak "
    "sıralandı: bu hatta o sırada servis sağlayıcının yönlendiricisi ikinci bir NAT yapmış "
    "olabilir."
)
METHOD_FALLBACK_DIRECT_EN: Final = (
    "{exit} is defined as direct, but around that moment {address} does not appear as a NAT "
    "address in the FortiGate logs, or only part of the time; the logs also show other, private "
    "or shared source-NAT addresses{on_line} then ({seen}). So no exact match was possible, and "
    "the sessions that left through {searched} were ranked as candidates: the ISP router may "
    "have done a second NAT on this line at that time."
)
ON_LINE: Final = (" bu hatta", " on this line")
METHOD_FALLBACK_TRANSIP_TR: Final = (
    "{exit} çıkışı servis sağlayıcı NAT'ı olarak tanımlı; NAT adresi {address} o anın "
    "çevresinde FortiGate kayıtlarında görünmüyor ya da yalnızca bir kısmında görünüyor; "
    "kayıtlar o sırada özel ya da paylaşılan başka kaynak NAT adresleri ({seen}) de gösteriyor. "
    "Bu nedenle kesin eşleşme yapılamadı ve {searched} adresleriyle çıkan oturumlar aday olarak "
    "sıralandı."
)
METHOD_FALLBACK_TRANSIP_EN: Final = (
    "Exit {exit} is defined as ISP NAT; around that moment its NAT address {address} does not "
    "appear in the FortiGate logs, or only part of the time; the logs also show other, private "
    "or shared source-NAT addresses then ({seen}). So no exact match was possible, and the "
    "sessions that left through {searched} were ranked as candidates."
)
#: The fallback addition when the search entered other exits' addresses (D15): exits as
#: converted without interfaces, or exits that share an interface with the failed one
#: (deviation d, round-5 note "fallback addition text").
METHOD_FALLBACK_OTHER_TR: Final = (
    "Aranan adreslerin bir kısmı {other} çıkışına aittir: bu çıkış yükseltmede eski ayarlardan "
    "aktarıldı ve arayüz tanımlanmadığı için aynı hat sayıldı. Liste o hattın kullanıcılarını da "
    "içerebilir; her hattın arayüzleri girilince bu olmaz."
)
METHOD_FALLBACK_OTHER_EN: Final = (
    "Some of the searched addresses belong to exit {other}: it was converted from the old "
    "settings at the upgrade and, with no interfaces defined, counted as the same line. The list "
    "may contain that line's users too; once each line's interfaces are entered this no longer "
    "happens."
)
METHOD_FALLBACK_SHARED_TR: Final = (
    "Aranan adreslerin bir kısmı {other} çıkışına aittir: bu çıkış {exit} çıkışıyla aynı "
    "arayüzü ({interfaces}) paylaştığı için aynı hat sayıldı. Liste o hattın kullanıcılarını da "
    "içerebilir."
)
METHOD_FALLBACK_SHARED_EN: Final = (
    "Some of the searched addresses belong to exit {other}: it shares an interface "
    "({interfaces}) with exit {exit}, so it was counted as the same line. The list may contain "
    "that line's users too."
)
METHOD_PUBLIC_UNKNOWN_TR: Final = (
    "{address} hiçbir çıkışın genel adresi olarak tanımlı değil. Arama, genel adresi "
    "tanımlanmamış ya da değişebilen çıkışlarda yapıldı: {exits}; bu çıkışlarda servis "
    "sağlayıcının ikinci bir NAT yaptığı varsayıldı. Adres bu hatlardan birine ait değilse liste "
    "ilgisiz kullanıcıları gösterebilir."
)
METHOD_PUBLIC_UNKNOWN_EN: Final = (
    "{address} is not the public address of any exit. The search was made in the exits whose "
    "public address is not defined or may change: {exits}; a second NAT by the ISP router was "
    "assumed for them. If the address belongs to none of these lines, the list may show "
    "unrelated users."
)
METHOD_UNCONFIGURED_TR: Final = (
    "İnternet çıkışları tanımlanmamış: servis sağlayıcının ikinci bir NAT yaptığı varsayıldı ve "
    "FortiGate'in özel ya da paylaşılan bir NAT adresiyle çıkan bütün kaynak NAT oturumları "
    "arandı; genel bir NAT adresiyle çıkan oturumlar FortiGate'in kendi adresinden çıktığı için "
    "aranmadı."
)
METHOD_UNCONFIGURED_EN: Final = (
    "No internet exits are defined: a second NAT by the ISP router was assumed and every "
    "source-NAT session of the FortiGate with a private or shared NAT address was searched; "
    "sessions that left with a public NAT address left through the FortiGate's own address and "
    "were not searched."
)
METHOD_NOT_LOGGED_TR: Final = (
    "{address}, {exit} çıkışına ait; bu çıkış FortiGate'ten geçmez ve ARCILOGGER'da bu hattın "
    "oturum kaydı yoktur. Bu nedenle rapor bir sonuç içermez; kayıt ancak hattı yöneten cihazdan "
    "alınabilir."
)
METHOD_NOT_LOGGED_EN: Final = (
    "{address} belongs to exit {exit}, which does not pass the FortiGate; ARCILOGGER has no "
    "session record of this line. The report therefore contains no result; records can only "
    "come from the device that runs the line."
)
METHOD_INTERNAL_TR: Final = (
    "{address}, {exit} tanımına göre FortiGate'in iç ağlara (DMZ, başka bir VLAN, VPN ya da "
    "şube) yaptığı kaynak NAT'ın adresidir; bir internet çıkışı değildir ve oturum aranmadı. Bu "
    "nedenle rapor bir sonuç içermez."
)
METHOD_INTERNAL_EN: Final = (
    "By the definition {exit}, {address} is the address of the FortiGate's source NAT towards "
    "internal networks (a DMZ, another VLAN, a VPN or a branch); it is not an internet exit and "
    "no session was searched. The report therefore contains no result."
)
#: "yapılandırılmış FortiGate kaynaklarının kayıtlarında" (deviation d, round-5 note "no_egress
#: after an HA failover": the address check reads the configured senders' rows only).
METHOD_NO_EGRESS_TR: Final = (
    "{address} kurumun tanımlı internet çıkışlarından hiçbirine ait değil ve o sırada "
    "yapılandırılmış FortiGate kaynaklarının kayıtlarında da görünmüyor; bu nedenle oturum "
    "aranmadı. Adres kurumun bir hattına aitse çıkış tanımlanıp sorgu yeniden "
    "çalıştırılmalıdır."
)
METHOD_NO_EGRESS_EN: Final = (
    "{address} belongs to none of the organisation's defined internet exits and does not appear "
    "in the records of the configured FortiGate sources at that time either, so no session was "
    "searched. If the address belongs to one of the organisation's lines, the exit must be "
    "defined and the lookup run again."
)
METHOD_CONFIGURED: Final = (
    "Sorgu anında tanımlı çıkışlar: {configured}.",
    "Exits defined at the time of the lookup: {configured}.",
)
#: Sections 4 and 5 of an answer that searched no session, in place of the generic "nothing
#: found" texts, which read as a log-based "nobody".
NOT_SEARCHED_NO_EGRESS: Final = (
    "Oturum aranmadı: istenen adres {address} tanımlı internet çıkışlarından hiçbirine ait değil "
    "(bölüm 4).",
    "No session was searched: the requested address {address} belongs to none of the defined "
    "internet exits (section 4).",
)
NOT_SEARCHED_NOT_LOGGED: Final = (
    "Oturum aranmadı: istenen adres {address} FortiGate'ten geçmeyen {exit} çıkışına ait "
    "(bölüm 4).",
    "No session was searched: the requested address {address} belongs to exit {exit}, which "
    "does not pass the FortiGate (section 4).",
)
NOT_SEARCHED_INTERNAL: Final = (
    "Oturum aranmadı: istenen adres {address} internet çıkışı olmayan {exit} tanımına ait "
    "(bölüm 4).",
    "No session was searched: the requested address {address} belongs to {exit}, which is not "
    "an internet exit (section 4).",
)
#: A match with rows of a sender that is no configured source (D24).
UNTRUSTED_MATCH: Final = (
    "Bu eşleşmenin kayıtları yapılandırmada tanımlı olmayan bir göndericiden geldi: {sources}; "
    "kurumun FortiGate'inden geldiği doğrulanmadan kanıt olarak kullanılmamalıdır.",
    "The records of this match came from a sender that is not configured: {sources}; do not use "
    "them as evidence until it is confirmed that they came from the organisation's FortiGate.",
)
#: The other exit of the session that changed exit (``session_changed_exit``), or, when the
#: stored outcome cannot tell which exits were this match's, its cited rows' NAT address and
#: dstintf (deviation d: the outcome stores the exit of the row nearest the moment only).
CHANGED_EXIT: Final = (
    "oturumun kayıtlarında ayrıca: {exits}",
    "the session's records also show: {exits}",
)
#: ``{exits}`` of :data:`CHANGED_EXIT` for the engine's "-": a row no exit claims (deviation d).
UNCLAIMED_EXIT: Final = (
    "hiçbir çıkışa tanımlı olmayan bir NAT adresi",
    "a NAT address no exit claims",
)
CHANGED_ROWS: Final = (
    "oturumun kayıtları birden çok çıkış gösteriyor: {rows}",
    "the session's records show more than one exit: {rows}",
)
#: A match's exit without a public IP in the request (a destination search): no exit was
#: determined, which "-" (no exit claims the NAT address) would misstate (deviation d).
EXIT_NOT_DETERMINED: Final = (
    "çıkış belirlenmedi (talepte genel IP yok)",
    "exit not determined (no public IP in the request)",
)
NO_CHAIN: Final = (
    "Zincir kurulamadı: o anda bu iç IP adresi için kira kaydı yok.",
    "No chain: no lease covered this internal IP address at that moment.",
)
#: The MAC step of a chain by the srcmac of a session record (2.9, D10).
SRCMAC_STEP: Final = (
    "MAC adresi (kısmi kanıt): FortiGate oturum kaydının srcmac alanı, {interface} arayüzü; "
    "FortiGate'in aygıt tanıma bilgisine göre kaynak IP adresi o anda bu MAC adresiyle "
    "ilişkilendirilmişti; kira kaydı yok",
    "MAC address (partial evidence): srcmac field of the FortiGate session record, interface "
    "{interface}; the FortiGate's device identification associated the source IP with this MAC "
    "address at that moment; no lease",
)
ALTERNATIVE_SRCMAC: Final = (
    "Olası diğer zincir (srcmac, kısmi kanıt) / Possible other chain (srcmac, partial evidence)"
)
#: The lease row of an ``fg_dhcp`` lease: the logged lease time and the lease's own last DHCP
#: record at or before the chain's moment (:data:`CONFIGURED_LEASE` without a logged time).
FG_LEASE: Final = (
    "kira süresi (FortiGate kaydı): {lease} · son DHCP kanıtı: {last}",
    "lease time (FortiGate record): {lease} · last DHCP evidence: {last}",
)
CONFIGURED_LEASE: Final = ("ayarlardaki süre", "configured time")
#: Chain notes printed beside the lease row instead of after the chain (2.12).
LEASE_NOTE_KEYS: Final = (
    "lease_renewal_not_seen",
    "fortigate_dhcp_not_converted_since_lease",
    "fortigate_dhcp_no_network",
)
#: Chain notes printed once in the method section instead: the stored verified text, else
#: :data:`FORMAT_UNVERIFIED` (2.12; never the setting at render time).
FORMAT_KEYS: Final = ("fortigate_format_verified", "fortigate_format_unverified")
FORMAT_UNVERIFIED: Final = (
    "FortiGate DHCP kayıt biçimi bu kurulumda sahada doğrulanmadı; kira kayıtları FortiOS "
    "belgelerindeki biçime göre okundu.",
    "The FortiGate DHCP record format has not been verified on site at this installation; the "
    "leases were read by the format of the FortiOS documentation.",
)
#: The mark of a cited FortiGate DHCP row that is not in the lease timeline (D8).
UNCONVERTED_MARK: Final = (
    "kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez",
    "not in the lease timeline; no chain built; no confidence given",
)
#: Its summary row in both languages, instead of the Turkish-only stored ``EvidenceRef.summary``.
UNCONVERTED_SUMMARY: Final = (
    f"FortiGate DHCP kaydı ({UNCONVERTED_MARK[0]})",
    f"FortiGate DHCP record ({UNCONVERTED_MARK[1]})",
)
SCORE_TR: Final = (
    "Aday puanı (0-95; kesin eşleşmeler 100): zaman 0-40 (an oturum aralığının içindeyse 40, "
    "toleransın sonuna doğru doğrusal olarak 0), hedef 0-50 (istenen IP ve port 50, alan adı "
    "35-50, IP 30-45, yalnız port 20, talepte hedef yoksa 25, eşleşmeyen hedef 0), isteğe bağlı "
    "port ipucu 0-5. Puan bir sıralamadır, olasılık değildir."
)
SCORE_EN: Final = (
    "Candidate score (0-95; exact matches 100): time 0-40 (40 inside the session interval, "
    "falling linearly to 0 at the tolerance), destination 0-50 (requested IP and port 50, domain "
    "35-50, IP 30-45, port only 20, 25 when the request names no destination, 0 when it does "
    "not match), optional port hint 0-5. The score ranks; it is not a probability."
)
EVIDENCE_INTRO_TR: Final = (
    "Her kanıt (K1, K2 …) bir veritabanı satırını ve dayandığı ham kayıt satırını gösterir. Ham "
    "satır, rapor oluşturulurken kaynak dosyadan (henüz mühürlenmemiş günler için spool dosyası, "
    "mühürlü günler için kasa/vault içindeki .log.gz dosyası) bayt bayt okunmuştur; satır "
    "numarası dosyanın başından sayılarak doğrulanmıştır. #012 gibi diziler, toplayıcının "
    "kontrol karakterleri ve # işareti için yazdığı kaçışlardır (# ve üç sekizlik basamak); "
    "UTF-8 olmayan baytlar \\xNN biçiminde gösterilir."
)
EVIDENCE_INTRO_EN: Final = (
    "Each evidence item (K1, K2 …) shows a database row and the raw log line it came from, read "
    "byte for byte when the report was made (spool file for days not sealed yet, the .log.gz in "
    "the vault for sealed days); the line number was verified by counting from the start of the "
    "file. Sequences such as #012 are the collector's escapes for control characters and '#' "
    "(# and three octal digits); bytes that are not UTF-8 are shown as \\xNN."
)
#: The re-check of every cited raw line against the values the report shows
#: (:mod:`arcilogger_api.reports.consistency`).
CONSISTENCY_TITLE: Final = "Ham satır karşılaştırması / Raw line check"
CONSISTENCY_TR: Final = (
    "Rapor oluşturulurken her kanıt satırı (bölüm 6) ARCILOGGER'ın ayrıştırıcısıyla yeniden "
    "okunmuş ve bu raporda veritabanından gösterilen değerlerle karşılaştırılmıştır: oturumun "
    "adresleri, portları, protokolü, oturum numarası ve NAT adresi/portu; IP-MAC kaydının IP ve "
    "MAC adresi; srcmac zincirinde oturum kaydının kaynak IP ve srcmac alanı; kaydın türü ve "
    "zamanı. Ham kayıt satırı kanıttır, veritabanı ondan türetilmiş veridir. Karşılaştırma, "
    "veritabanının kayıtlar alındıktan sonra değişmesini (elle düzeltme, hatalı yeniden "
    "hesaplama, müdahale) ortaya çıkarır; ayrıştırıcının kendi hatasını ortaya çıkaramaz. "
    "Cihazın bir kişiye bağlanması hiçbir log satırına dayanmaz."
)
CONSISTENCY_EN: Final = (
    "Every evidence line (section 6) was parsed again with ARCILOGGER's parser when the report "
    "was made and compared with the values this report shows from the database: addresses, "
    "ports, protocol, session number and NAT address/port of a session; IP and MAC address of an "
    "IP-MAC record; source IP and srcmac of the session record of a srcmac chain; record type "
    "and time. The raw log line is the evidence, the database is derived from it. The check "
    "finds a database changed after the logs were received (manual repair, faulty rebuild, "
    "tampering); it cannot find a defect of the parser itself. Linking a device to a person "
    "rests on no log line."
)
CONSISTENCY_OK_TR: Final = (
    "Sonuç: karşılaştırılan {checked} ham satırın tamamı rapordaki değerlerle uyuşuyor. / All "
    "{checked} lines checked match."
)
CONSISTENCY_NONE_TR: Final = (
    "Karşılaştırılacak ham satır yok (bkz. bölüm 6). / No raw line could be checked."
)
CONSISTENCY_FAILED_TR: Final = (
    "UYARI: {refs} numaralı kanıt satırları, bu raporda veritabanından gösterilen değerlerle "
    "uyuşmuyor. Ham kayıt satırı kanıttır, veritabanı türetilmiş veridir: bu sorgunun sonucu "
    "mühürlü ham kayıtlarla desteklenmiyor ve bu haliyle kullanılmamalıdır. Sistem yöneticisi "
    "veritabanını ve ham kayıtları incelemelidir; farklar bölüm 6'da her kanıtın altındadır."
)
CONSISTENCY_FAILED_EN: Final = (
    "WARNING: evidence lines {refs} do not match the values this report shows from the "
    "database. The raw log line is the evidence and the database is derived: this result is not "
    "supported by the sealed raw log and must not be used as it is. An administrator must "
    "examine the database and the raw logs; the differences are listed in section 6."
)
#: Shown instead of "kesin eşleşme" and instead of the chain's confidence.
CONTRADICTED_TR: Final = "KANITLA UYUŞMAYAN SONUÇ"
CONTRADICTED_EN: Final = "result contradicted by its evidence"
CHECK_ERRORS: Final[Mapping[str, str]] = {
    "not_a_raw_line": "satır ham kayıt biçiminde değil",
    "other_record_type": (
        "satır, kanıt olarak gösterilen türde bir kayıt değil (ayrıştırıcı bu satırdan böyle bir "
        "kayıt üretmez)"
    ),
}
CHECK_FIELDS: Final[Mapping[str, str]] = {
    "srcip": "kaynak IP",
    "srcport": "kaynak port",
    "dstip": "hedef IP",
    "dstport": "hedef port",
    "proto": "protokol",
    "sessionid": "FortiGate oturum no",
    "transip": "NAT IP (transip)",
    "transport": "NAT port (transport)",
    "ip": "IP adresi",
    "mac": "MAC adresi",
    "time": "kayıt zamanı",
    "srcmac": "kaynak MAC (srcmac)",
}
VERIFY_TR: Final = (
    'Bu PDF dosyasının SHA-256 özeti ("ARCILOGGER imza özeti") dosyanın içinde yer alamaz; '
    "ARCILOGGER'da sorgu kaydında ve hash zincirli denetim kaydında (işlem lookup.report) "
    "saklanır ve indirme sırasında gösterilir. Dosyanın değişmediğini doğrulamak için özeti "
    "yeniden hesaplayın — Windows: certutil -hashfile <dosya> SHA256, Linux/macOS: sha256sum "
    "<dosya> — ve ARCILOGGER'daki değerle karşılaştırın. Rapor bir kez üretilir ve her indirmede "
    "aynı dosya verilir."
)
VERIFY_EN: Final = (
    'The SHA-256 of this PDF ("ARCILOGGER signature digest") cannot be inside the file itself; '
    "ARCILOGGER keeps it with the lookup and in the hash-chained audit log (action "
    "lookup.report) and shows it on download. To check that the file is unchanged, recompute it "
    "(Windows: certutil -hashfile <file> SHA256, Linux/macOS: sha256sum <file>) and compare. The "
    "report is generated once; every download returns the same file."
)

MODES: Final[Mapping[str, tuple[str, str]]] = {
    "exact": ("Kesin eşleşme (FortiGate NAT kaydı)", "Exact match (FortiGate NAT record)"),
    "candidates": (
        "Aday listesi (zamana ve hedefe göre sıralı)",
        "Candidate list (ranked by time and destination)",
    ),
    "internal": ("İç IP / MAC sorgusu (NAT adımı yok)", "Internal IP / MAC lookup (no NAT step)"),
    "none": ("Sonuç yok", "No result"),
}
MODE_DETAIL_TR: Final[Mapping[str, str]] = {
    "exact": (
        "Talepteki genel IP adresi FortiGate'in kendi genel/WAN adresidir; transip:transport "
        "talepteki adres ve portla birebir eşleşen oturumlar listelenir."
    ),
    "candidates": (
        "Talep kesin bir NAT eşleşmesine izin vermiyor; zaman penceresindeki oturumlar aday "
        "olarak sıralanmıştır."
    ),
    "internal": (
        "Talep doğrudan bir iç IP adresi veya MAC adresi içeriyor; yalnızca IP-MAC zaman "
        "çizelgesi ve cihaz/kişi zinciri gösterilir."
    ),
    "none": "Ölçütlere uyan kayıt bulunamadı; nedenleri aşağıdaki açıklamalardadır.",
}
#: The exact answer on an exit's NAT address (resolution ``transip``), which may be a shared
#: (RFC 6598) or private address, never called "genel" (deviation d).
MODE_DETAIL_TRANSIP_TR: Final = (
    "Talepteki adres FortiGate'in bu çıkıştaki kendi NAT (WAN) adresidir; transip:transport "
    "talepteki adres ve portla birebir eşleşen oturumlar listelenir."
)
DESTINATION: Final[Mapping[str, str]] = {
    "ip_port": "hedef IP ve port eşleşti",
    "ip": "hedef IP eşleşti",
    "ip_other_port": "hedef IP eşleşti, port farklı",
    "domain": "alan adı eşleşti",
    "domain_other_port": "alan adı eşleşti, port farklı",
    "domain_address": (
        "yalnızca adres eşleşti: alan adı bu istemcinin kayıtlarında görülmedi, adres başka bir "
        "istemcinin kaydından biliniyor (paylaşılan CDN adresi olabilir)"
    ),
    "domain_unverified": (
        "hedef doğrulanmadı: oturum NAT adresi ve portuyla belirlendi, talepteki alan adıyla "
        "ilişkisi kayıtlardan gösterilemedi"
    ),
    "port": "hedef port eşleşti",
    "none": "hedef eşleşmedi",
    "not_requested": "talepte hedef yok",
}
CONFIDENCE: Final[Mapping[str, str]] = {"high": "yüksek", "medium": "orta", "low": "düşük"}
#: ``superseded`` has one neutral label for every cause: another MAC took the address, the
#: lease's MAC took another address (D33), or the other DHCP table's ACK of the same pair (D5).
END_REASONS: Final[Mapping[str, str]] = {
    "dhcp": "DHCP bırakma (deassigned)",
    "timeout": "kira süresi doldu (zaman aşımı)",
    "superseded": "sonraki bir DHCP atamasıyla kapandı / closed by a later DHCP assignment",
    "arp": "ARP-LOG aralığı sona erdi",
    "open": "açık (sürüyor)",
}
DEPARTMENT_SOURCES: Final[Mapping[str, str]] = {
    "person": "kişinin departmanı",
    "vlan": "VLAN'ın departmanı",
    "none": "bilinmiyor",
}
DEVICE_TYPES: Final[Mapping[str, str]] = {
    "pc": "PC",
    "phone": "telefon",
    "printer": "yazıcı",
    "ip_phone": "IP telefon",
    "ap": "erişim noktası",
    "server": "sunucu",
    "camera": "kamera",
    "other": "diğer",
}
PROTOCOLS: Final[Mapping[int, str]] = {1: "ICMP", 6: "TCP", 17: "UDP"}
LINE_STATUS: Final[Mapping[str, str]] = {
    "missing": "Satır dosyada bulunamadı (dosyada daha az satır var).",
    "offset_mismatch": "UYARI: satır, veritabanında kayıtlı bayt konumunda başlamıyor.",
    "no_reference": "Bu kayıt için ham satır referansı yok.",
    "invalid": "Geçersiz kaynak adı; dosya okunmadı.",
    "unreadable": "Ham dosya okunamadı.",
}
FILE_STATUS: Final[Mapping[str, str]] = {
    "sealed": "Mühürlü (kasa/vault)",
    "unsealed": "Henüz mühürlenmedi (spool) — özet rapor anındaki dosyanındır",
    "seal_missing": (
        "MÜHÜR BULUNAMADI — dosya kasada (vault), fakat günün manifesti veya zaman damgası yok "
        "(bkz. bölüm 6.3)"
    ),
    "missing": "Ham dosya bulunamadı (saklama süresi dolmuş veya taşınmış olabilir)",
    "invalid": "Geçersiz kaynak adı",
    "unreadable": "Dosya okunamadı",
}
TABLES: Final[Mapping[str, str]] = {
    "fg_traffic": "FortiGate oturum kaydı",
    "fg_web": "FortiGate web filtresi kaydı",
    "fg_app": "FortiGate uygulama kaydı",
    "fg_dns": "FortiGate DNS kaydı",
    "mt_dhcp": "MikroTik DHCP kaydı",
    "mt_arp": "MikroTik ARP-LOG kaydı",
    "fg_dhcp": "FortiGate DHCP kaydı",
    "fg_event": "FortiGate olay kaydı",
}
#: English beside the Turkish table label, for the tables added by ADR-0016 (the older labels
#: stay Turkish only, as reports printed them).
TABLES_EN: Final[Mapping[str, str]] = {
    "fg_dhcp": "FortiGate DHCP record",
    "fg_event": "FortiGate event record",
}
SEAL_MISSING_TR: Final = (
    "MÜHÜR BULUNAMADI: bu günün ham kayıt dosyaları kasaya (vault) taşınmış, fakat günün "
    "manifesti ve/veya zaman damgası kasada yok. Mühür silinmiş, taşınmış veya mühürleme yarıda "
    "kalmış olabilir; bu günün kayıtlarının değişmediği ARCILOGGER ile kanıtlanamaz. Yedek "
    "kopyayı (replica) ve Mühürleme sayfasını kontrol edin."
)
SEAL_MISSING_EN: Final = (
    "SEAL NOT FOUND: the raw files of this day were moved to the vault, but the day's manifest "
    "and/or time-stamp token are not there. The seal may have been deleted or moved, or sealing "
    "stopped half-way; ARCILOGGER cannot show that this day's records are unchanged. Check the "
    "replica and the Sealing page."
)
UNSEALED_TR: Final = (
    "Henüz mühürlenmedi / not sealed yet. Bu günün dosyaları gece mühürleme işinden sonra "
    "Mühürleme sayfasında doğrulanabilir; dosyanın rapor anındaki SHA-256 özeti bölüm 6.2'dedir."
)
UNSEALED_LATE_TR: Final = (
    "UYARI: gün raporun oluşturulmasından en az iki gün önce kapandı; gece mühürleme işi bu günü "
    "henüz mühürlememiş (gecikmiş). Mühürleme sayfasını kontrol edin. / WARNING: sealing of this "
    "day is overdue."
)
#: Characters of a raw line the PDF shows; longer lines are cut there (with a warning), their
#: SHA-256 and length always describe the whole line. 72 KiB: a message of the default
#: ``collector.max_message_bytes`` (64 KiB) with its line prefix and some escapes is shown whole
#: (about 10 pages); the setting allows up to 1 MiB, which would fill well over 100 pages.
RAW_DISPLAY_CHARS: Final = 73_728
