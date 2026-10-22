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
    "istenen zamandaki oturum bulunur; (2) MikroTik DHCP ve saatlik ARP-LOG kayıtlarından "
    "hesaplanan IP-MAC zaman çizelgesiyle iç IP adresinin o anda hangi MAC adresinde olduğu, "
    "cihaz kaydı ve (varsa) cihazın bağlı olduğu kişi ve departman belirlenir. Her adım, "
    "dayandığı veritabanı satırını ve ham kayıt satırını (kanıt listesi, bölüm 6) gösterir."
)
METHOD_EN: Final = (
    "ARCILOGGER writes every syslog message unchanged to a raw log file the moment it arrives and "
    "parses the same line into the database. A lookup has two steps: (1) the source NAT of the "
    "FortiGate session logs (internal IP:port → transip:transport) finds the session at the "
    "requested time; (2) the IP-MAC timeline computed from the MikroTik DHCP and hourly ARP-LOG "
    "records gives the MAC address that held the internal IP at that moment, the device record "
    "and, if linked, the person and department. Every step cites its database row and raw log "
    "line (evidence list, section 6)."
)
#: Double NAT (spec 12.4): why the answer cannot be exact, then — by mode — what the result is,
#: then what would make the answer exact. The "candidate list" sentence appears only when
#: candidates follow (docs/MVP-CONTRACTS.md section 5: ``double_nat`` vs
#: ``double_nat_no_candidates``).
DOUBLE_NAT_TR: Final = (
    "Çift NAT: FortiGate'in WAN arayüzü ISS modeminin arkasındadır ve modem ikinci bir adres "
    "dönüşümü yapar. ARCILOGGER'da bu ikinci dönüşümün kaydı yoktur; bu nedenle talepteki genel "
    "port, FortiGate'in çıkış portuna (transport) kesin olarak eşlenemez."
)
DOUBLE_NAT_EN: Final = (
    "Double NAT: the FortiGate's WAN interface sits behind the ISP router, which translates the "
    "address a second time. ARCILOGGER has no record of that second translation, so the public "
    "port of the request cannot be mapped exactly to the FortiGate's outgoing port (transport)."
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
DOUBLE_NAT_EXACT_TR: Final = (
    "Kesin eşleşme için şunlardan biri gerekir: (a) ISS modeminin ilgili zamana ait NAT kaydı "
    "(genel port → FortiGate çıkış portu); (b) modemin köprü (bridge) moduna alınması ve "
    "FortiGate'in genel IP adresini doğrudan tutması (ARCILOGGER Ayarlar → NAT: genel IP). "
    "Talepte hedef IP, hedef port veya alan adının ve saat diliminin bulunması aday sayısını "
    "büyük ölçüde azaltır."
)
DOUBLE_NAT_EXACT_EN: Final = (
    "An exact answer needs either (a) the ISP router's NAT record for that time (public port → "
    "FortiGate port) or (b) the ISP router in bridge mode with the public address on the "
    "FortiGate (ARCILOGGER Settings → NAT: public IP). A destination IP, port or domain and the "
    "time zone in the request narrow the candidates considerably."
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
    "MAC adresi; kaydın türü ve zamanı. Ham kayıt satırı kanıttır, veritabanı ondan türetilmiş "
    "veridir. Karşılaştırma, veritabanının kayıtlar alındıktan sonra değişmesini (elle "
    "düzeltme, hatalı yeniden hesaplama, müdahale) ortaya çıkarır; ayrıştırıcının kendi hatasını "
    "ortaya çıkaramaz. Cihazın bir kişiye bağlanması hiçbir log satırına dayanmaz."
)
CONSISTENCY_EN: Final = (
    "Every evidence line (section 6) was parsed again with ARCILOGGER's parser when the report "
    "was made and compared with the values this report shows from the database: addresses, "
    "ports, protocol, session number and NAT address/port of a session; IP and MAC address of an "
    "IP-MAC record; record type and time. The raw log line is the evidence, the database is "
    "derived from it. The check finds a database changed after the logs were received (manual "
    "repair, faulty rebuild, tampering); it cannot find a defect of the parser itself. Linking a "
    "device to a person rests on no log line."
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
END_REASONS: Final[Mapping[str, str]] = {
    "dhcp": "DHCP bırakma (deassigned)",
    "timeout": "kira süresi doldu (zaman aşımı)",
    "superseded": "IP başka bir MAC adresine verildi",
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
