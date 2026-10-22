"""``DOGRULAMA.txt`` (Turkish) and ``VERIFY-EN.txt`` (English) for an evidence package.

The text names the exact files of the package, so an auditor can copy the commands. What it says
about Kamu SM's Zamane client is limited to the usage Kamu SM documents (``-c`` and ``-CC``, see
``docs/kamusm.md``); the client's output format is not documented and is not described here.
Both files are UTF-8 with a byte order mark and CRLF line ends, so Windows Notepad shows the
Turkish letters correctly on every Windows version; the file names are ASCII so that every ZIP
tool unpacks them under the name the package index lists.

The guide does not ask the reader to trust the ``verify.py`` that comes in the same package:
method C checks the time stamps, the files, the chain and the extract with OpenSSL and standard
hash tools only, and the verifier's SHA-256 is given so it can be confirmed elsewhere.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from arcilogger_core.fmt import format_date, format_datetime

ZAMANE_PAGE = (
    "https://kamusm.bilgem.tubitak.gov.tr/urunler/zaman_damgasi/"
    "ucretsiz_zaman_damgasi_istemci_yazilimi.jsp"
)
ZAMANE_JAR = "tss-client-console-3.1.33.jar"
LEGAL_NOTE_TR = (
    "ARCILOGGER, 5651 sayılı Kanun ve İnternet Toplu Kullanım Sağlayıcıları Hakkında "
    "Yönetmelik kapsamındaki yükümlülükleri desteklemek için tasarlanmıştır. Bu paketin hukuki "
    "değerlendirmesi için bir hukuk danışmanına başvurulmalıdır."
)
LEGAL_NOTE_EN = (
    "ARCILOGGER is designed to support the obligations of Turkish Law 5651; it does not claim "
    "legal compliance. Have this package assessed by a lawyer."
)


@dataclass(frozen=True)
class DayFacts:
    day: date
    folder: str  # package path of the day folder, e.g. vault/2026/10/12
    manifest: str  # file name only
    token: str
    container: str  # "TimeStampResp" or "TimeStampToken"
    gen_time: datetime
    serial: str
    tsa: str
    provider: str
    #: Stamped by the Kamu SM production server and chained to a root the site listed as a
    #: Kamu SM root (tsa.legal_root_fingerprints) — never from the manifest's claim alone.
    legal_grade: bool
    files: Sequence[str]  # package paths of the included sealed files
    #: The manifest says the day was stamped by the Kamu SM production server.
    legal_grade_claimed: bool = False


@dataclass(frozen=True)
class RootFacts:
    path: str
    subject: str
    sha256: str  # upper-case hex
    sha1: str
    not_before: datetime
    not_after: datetime


@dataclass(frozen=True)
class GuideFacts:
    package_id: str
    created_at: datetime
    created_by: str
    site: str
    version: str
    days: Sequence[DayFacts]
    sources: Sequence[str] | None
    lookup_id: str | None
    reference: str | None
    roots: Sequence[RootFacts]
    roots_bundle: str
    extract_files: Sequence[str]
    report: str | None
    crl_files: Sequence[str]
    encrypted: bool
    extra_records: Sequence[str]  # deletion records, chain anchor, renewals
    verifier_sha256: str = ""
    #: ``rapor/<name>.pdf.zd`` when the report was time-stamped.
    report_token: str | None = None
    #: Why the report is in the package without its time stamp (bilingual), if it is.
    report_note: str | None = None
    #: The days' ARCILOGGER audit logs are in the package (off by default: KVKK).
    audit_log_included: bool = False


def colon(hex_text: str) -> str:
    return ":".join(hex_text[i : i + 2] for i in range(0, len(hex_text), 2))


def openssl_command(day: DayFacts, bundle: str) -> str:
    """``openssl ts -verify`` for one day, run from the package folder.

    ``-attime`` checks the certificates at the stamp time, so the command still works after the
    TSA certificate has expired; ``-token_in`` is needed when the ``.zd`` file is a bare token.
    """
    token_in = " -token_in" if day.container == "TimeStampToken" else ""
    return (
        f"openssl ts -verify{token_in} -in {day.folder}/{day.token} "
        f"-data {day.folder}/{day.manifest} -CAfile {bundle} "
        f"-attime {int(day.gen_time.timestamp())}"
    )


def _windows(path: str) -> str:
    return path.replace("/", "\\")


def hash_commands(day: DayFacts) -> tuple[str, str]:
    """``sha256sum`` (Linux) and ``Get-FileHash`` (PowerShell) for the sealed files of a day."""
    return (
        f"sha256sum {day.folder}/*.gz",
        f"Get-FileHash -Algorithm SHA256 {_windows(day.folder)}\\*.gz",
    )


def chain_commands(days: Sequence[DayFacts]) -> list[tuple[DayFacts, str]]:
    """``(day, sha256sum …)``: the hashes of the previous day's manifest and token, for every
    day whose previous day is also in the package."""
    pairs = []
    for day in days:
        before = next((d for d in days if (day.day - d.day).days == 1), None)
        if before is not None:
            command = f"sha256sum {before.folder}/{before.manifest} {before.folder}/{before.token}"
            pairs.append((day, command))
    return pairs


def extract_command(extract_file: str, line: str = "N") -> str:
    """``gzip -dc <sealed file> | sed -n 'Np'`` for an extract file ``extract/DAY/SOURCE.txt``."""
    day, source = extract_file.split("/")[1:3]
    sealed = f"vault/{day[:4]}/{day[5:7]}/{day[8:10]}/{source.removesuffix('.txt')}.log.gz"
    return f"gzip -dc {sealed} | sed -n '{line}p'"


def zamane_commands(day: DayFacts) -> list[str]:
    return [
        f"cd {day.folder}",
        f"java -jar <KLASÖR>/{ZAMANE_JAR} -CC {day.manifest} {day.token}",
    ]


def _encode(lines: Sequence[str]) -> bytes:
    return ("﻿" + "\r\n".join(lines) + "\r\n").encode("utf-8")


def _days_span(facts: GuideFacts) -> tuple[str, str]:
    first, last = facts.days[0].day, facts.days[-1].day
    return format_date(first), format_date(last)


def render_turkish(facts: GuideFacts) -> bytes:
    first, last = _days_span(facts)
    test_days = [format_date(day.day) for day in facts.days if not day.legal_grade_claimed]
    unconfirmed = [
        format_date(day.day)
        for day in facts.days
        if day.legal_grade_claimed and not day.legal_grade
    ]
    lines = [
        "ARCILOGGER KANIT PAKETİ — DOĞRULAMA KILAVUZU",
        "=" * 44,
        "",
        f"Paket             : {facts.package_id}",
        f"Oluşturulma       : {format_datetime(facts.created_at)} (Türkiye saati, UTC+03:00)",
        f"Oluşturan         : {facts.created_by or '-'}",
        f"Kurum             : {facts.site or '-'}",
        f"Günler            : {first} - {last} ({len(facts.days)} gün)",
        f"Kaynaklar         : {', '.join(facts.sources) if facts.sources else 'tümü'}",
        f"Sorgu no          : {facts.lookup_id or '-'}",
        f"Referans          : {facts.reference or '-'}",
        f"ARCILOGGER sürümü : {facts.version}",
        "",
        *textwrap.wrap(LEGAL_NOTE_TR, 92),
        "",
        "1. BU PAKET NEYİ GÖSTERİR?",
        "-" * 26,
        "ARCILOGGER, ağ cihazlarından (FortiGate, MikroTik vb.) gelen her syslog mesajını alındığı",
        "anda, değiştirmeden, alınma zamanı ve gönderen IP adresiyle birlikte ham kayıt dosyasına",
        "yazar. Her gün bittikten sonra:",
        "  a) o günün ham dosyaları sıkıştırılır (gzip) ve her dosyanın SHA-256 özeti hesaplanır;",
        "  b) özetler, bir önceki günün manifest ve zaman damgası özetleriyle birlikte günlük",
        "     manifest dosyasına yazılır; böylece günler birbirine zincirlenir;",
        "  c) manifestin SHA-256 özeti TÜBİTAK BİLGEM Kamu SM zaman damgası sunucusuna gönderilir",
        "     ve dönen RFC 3161 zaman damgası (.zd dosyası) saklanır. Kamu SM'ye yalnızca özet",
        "     gider, kayıtların kendisi gitmez.",
        "Bu paket doğrulandığında şunlar gösterilmiş olur:",
        "  - her manifest, zaman damgasında yazan anda (genTime) vardı ve o andan beri değişmedi;",
        "  - manifestte özeti bulunan ve pakette yer alan her kayıt dosyası, mühürlendiği andan",
        "    beri bayt bayt aynıdır;",
        "  - pakette art arda bulunan günler arasındaki zincir kopmamıştır (bir gün sonradan",
        "    değiştirilseydi ertesi günün manifestindeki özet tutmazdı); pakette atlanan gün",
        "    varsa verify.py bunu uyarı olarak bildirir;",
        "  - extract klasöründeki satırlar, mühürlü dosyalardaki aynı numaralı satırların bayt",
        "    bayt kopyasıdır;",
        "  - her günün damgasının gün bitiminden ne kadar sonra alındığı damganın kendisinden",
        "    hesaplanır; 48 saatten geç alınan damgalar uyarı olarak gösterilir (geç damga,",
        "    günün damgalanmadan önce değiştirilmiş olabileceği anlamına gelir).",
        "",
        "2. BU PAKET NEYİ GÖSTERMEZ?",
        "-" * 27,
        "  - Kayıtları kimin ürettiğini ya da cihazların doğru kayıt ürettiğini kanıtlamaz; zaman",
        "    damgası verinin kimin tarafından oluşturulduğunu değil, belirli bir anda var olduğunu",
        "    kanıtlar.",
        "  - Gün içinde, mühürlemeden önce değişiklik yapılmadığını tek başına kanıtlamaz; bunun",
        "    için sunucunun işletim ve güvenlik kayıtları ayrıca değerlendirilmelidir.",
        "  - Satırlardaki alınma zamanları sunucu saatine dayanır; saat eşitleme bilgisi",
        '    manifestin "clock" bölümündedir.',
        "  - Bir IP adresinin belirli bir kişiye ait olduğunu tek başına kanıtlamaz; bu bir",
        "    analizdir ve (varsa) rapordaki yöntemle birlikte değerlendirilmelidir.",
        "  - verify.py, paketteki kök sertifikaların gerçekten Kamu SM'ye ait olduğunu kendi",
        "    başına kanıtlayamaz (bkz. bölüm 7).",
        "  - verify.py bu paketin içinde gelir: paketi değiştirebilen biri onu da değiştirebilir.",
        "    Bu yüzden ya özetini paketten bağımsız bir kanaldan doğrulayın ya da Yöntem C'yi",
        "    uygulayın (bkz. bölüm 4 ve 6).",
        "  - Sertifika iptal durumu çevrim içi (SİL indirme, OCSP) kontrol edilmez.",
        "    "
        + (
            "Paketteki SİL dosyaları verify.py tarafından kontrol edilir."
            if facts.crl_files
            else "Bu pakette SİL yoktur."
        ),
        "  - Kapsam dışı bırakılan kaynakların dosyaları pakette yoktur ve doğrulanmaz.",
    ]
    if test_days:
        lines += [
            "",
            "UYARI: Şu günler yasal geçerliliği OLMAYAN bir test (deneme) zaman damgasıyla",
            f"mühürlenmiştir: {', '.join(test_days)}. Bu günler teknik olarak doğrulanabilir,",
            "ancak Kamu SM zaman damgası taşımaz.",
        ]
    if unconfirmed:
        lines += [
            "",
            "NOT: Şu günlerin damgası Kamu SM üretim sunucusundan alınmış olarak kayıtlıdır, ancak",
            "damganın bağlandığı kök sertifikanın parmak izi ARCILOGGER'da Kamu SM kökü olarak",
            f"tanımlanmamıştır: {', '.join(unconfirmed)}. Kökün parmak izini Kamu SM'nin",
            "yayımladığı değerle karşılaştırın (bölüm 7).",
        ]
    lines += [
        "",
        "3. PAKETİN İÇERİĞİ",
        "-" * 18,
        "  paket.json                 Paket dizini: her dosyanın boyutu ve SHA-256 özeti, kapsam,",
        "                             kök sertifika parmak izleri (kanonik JSON, RFC 8785).",
        "  DOGRULAMA.txt              Bu kılavuz (VERIFY-EN.txt: İngilizce kısa sürüm).",
        "  verify.py                  Bağımsız doğrulayıcı; yalnızca Python 3 standart",
        "                             kütüphanesini kullanır.",
        "  verify.ps1, verify.sh      verify.py'yi Windows / Linux'ta çalıştıran kısa betikler.",
        "  vault/YYYY/MM/DD/          Mühürlü günler (sunucudaki klasör yapısıyla aynı):",
        "    <kaynak>.log.gz          günün ham kayıt dosyası (gzip; içerik değiştirilmemiş),",
    ]
    if facts.audit_log_included:
        lines.append("    audit.jsonl.gz           ARCILOGGER'ın o günkü denetim kaydı,")
    lines += [
        "    manifest-YYYY-MM-DD.json         günlük manifest,",
        "    manifest-YYYY-MM-DD.json.zd      manifestin zaman damgası (RFC 3161).",
    ]
    if not facts.audit_log_included:
        lines += [
            "  ARCILOGGER'ın günlük denetim kaydı (audit.jsonl.gz) bilinçli olarak pakete",
            "  alınmamıştır: başka sorguları ve personel bilgilerini içerir (KVKK veri en aza",
            "  indirme). Manifestte listelenir; SHA-256 değeri paket.json'da (excluded) yer alır.",
        ]
    if facts.extra_records:
        lines.append(
            "  vault/deletions/, vault/renewals/  İmha tutanakları, zincir çapası, yenileme"
        )
        lines.append("                             damgaları.")
    if facts.extract_files:
        lines += [
            "  extract/GÜN/<kaynak>.txt   İlgili ham satırlar; her satır:",
            "                             <satır no><TAB><ham satır>. Satır numarası, mühürlü",
            "                             dosyanın açılmış halindeki sıradır (1'den başlar).",
        ]
    lines += [
        "  sertifikalar/kokler/       Kök sertifikalar (PEM); sertifikalar/kokler.pem hepsini",
        "                             tek dosyada içerir (OpenSSL için).",
        "  sertifikalar/tsa/          Zaman damgalarından çıkarılan TSA sertifikaları.",
    ]
    if facts.crl_files:
        lines.append(
            "  sil/                       Sertifika iptal listeleri (SİL), alındıkları haliyle."
        )
    if facts.report:
        lines += [
            f"  {facts.report:<26} ARCILOGGER'ın 5651 sorgu raporu (Resmi Rapor), ARCILOGGER'da",
            "                             saklanan dosyanın aynısı. SHA-256 özeti paket.json'da;",
            "                             ARCILOGGER'daki sorgu kaydı ve denetim kaydındaki",
            "                             (lookup.report) değerle aynı olmalıdır.",
        ]
    if facts.report_token:
        lines += [
            f"  {facts.report_token:<26} Raporun zaman damgası (RFC 3161); verify.py denetler,",
            "                             ya da: openssl ts -verify -in <rapor>.pdf.zd -data",
            "                             <rapor>.pdf -CAfile sertifikalar/kokler.pem",
        ]
    if facts.report_note:
        lines.append("                             Raporun zaman damgası pakette yok:")
        lines += [" " * 29 + part for part in textwrap.wrap(facts.report_note, 62)]
    lines += [
        "",
        "Ham satır biçimi: <alınma zamanı> <gönderen IP> <mesaj>. Mesajdaki kontrol karakterleri",
        "ve # işareti, # ve üç sekizlik rakamla yazılır (satır sonu #012, # işareti #043); diğer",
        "bütün baytlar cihazdan geldiği gibidir.",
        "ZIP içindeki dosya tarihleri bilinçli olarak 01.01.1980'dir (aynı girdilerden aynı paket",
        "üretilebilsin diye); gerçek zamanlar zaman damgalarında ve paket.json'dadır.",
        "",
        "4. YÖNTEM A — verify.py ile doğrulama (önerilen)",
        "-" * 48,
        "  1) Python 3.9 veya üstünü kurun (https://www.python.org/downloads/). Başka paket",
        "     gerekmez; doğrulayıcı ağa bağlanmaz ve dosya yazmaz.",
    ]
    if facts.encrypted:
        lines += [
            "  2) Paket parolalıdır (AES-256). AES-256 destekleyen bir araçla (örneğin 7-Zip)",
            "     parolayı girerek bir klasöre açın. Windows'un yerleşik ZIP açıcısı AES-256",
            "     şifreli paketleri açamayabilir. Dosya adları şifrelenmez, içerikleri şifrelenir.",
        ]
    else:
        lines += ["  2) Paketi bir klasöre açın (ya da ZIP dosyasını doğrudan verin)."]
    lines += [
        "  3) Klasörde bir komut penceresi açıp çalıştırın:",
        "       Windows : py -3 -I -S verify.py",
        "                 ya da: powershell -ExecutionPolicy Bypass -File .\\verify.ps1",
        "       Linux   : python3 -I -S verify.py   (ya da: sh verify.sh)",
        "     -I -S, Python'u yalıtır: bilgisayarda kurulu başka paketler kullanılmaz.",
        '  4) Son satır "SONUÇ: DOĞRULANDI" ile başlıyorsa hiçbir kontrol başarısız olmamıştır;',
        "     [UYARI] satırlarını (örneğin geç damga, pakette eksik gün, test damgası) ayrıca",
        '     okuyun. "DOĞRULANAMADI" ise [HATA] satırları sorunun yerini gösterir. Çıkış kodu:',
        "     0 doğrulandı, 1 hata var, 2 paket okunamadı (güvenli olmayan ya da büyük/küçük",
        "     harf farkıyla çakışan dosya adları dahil).",
        "  Seçenekler: --kokler KLASÖR (yalnızca bu klasördeki köklere güven, örneğin Kamu SM'den",
        "  kendi indirdikleriniz), --hizli (gzip dosyalarını açmadan yalnızca özetleri kontrol",
        "  et), --json (makine tarafından okunabilir çıktı).",
        "  ÖNEMLİ: verify.py bu paketle birlikte gelir; paketi değiştirebilen biri verify.py'yi de",
        "  değiştirip her zaman DOĞRULANDI yazdırabilir. Sonuca ancak şu durumlarda güvenin:",
        "    - verify.py'nin SHA-256 özetini paketi size teslim edenden AYRI bir kanaldan (ör.",
        "      teslim tutanağı, resmî yazı) aldığınız değerle karşılaştırdınız; bu paketteki",
        f"      verify.py'nin SHA-256 özeti: {facts.verifier_sha256 or '-'}",
        "      (Windows: certutil -hashfile verify.py SHA256, Linux: sha256sum verify.py); ya da",
        "    - kendi edindiğiniz bir verify.py kopyasını pakete karşı çalıştırdınız:",
        "      python -I -S <kendi klasörünüz>/verify.py <paket klasörü>",
        "  Bunlar mümkün değilse Yöntem C'yi uygulayın; Yöntem C verify.py'ye dayanmaz.",
        "",
        "5. YÖNTEM B — Kamu SM'nin resmî Zamane istemcisi",
        "-" * 48,
        '  Kamu SM\'nin "Ücretsiz Zaman Damgası İstemci Yazılımı" sayfasından konsol istemcisini',
        f"  ({ZAMANE_JAR}, Java 1.8 gerekir) indirin ve indirdiğiniz dosyanın SHA-256",
        "  değerini sayfada yayımlanan değerle karşılaştırın:",
        f"    {ZAMANE_PAGE}",
        '  Kamu SM\'nin belgelediği kullanım: "-c <dosya> <dosya>.zd" damgayı kontrol eder,',
        '  "-CC" ayrıca sunucu sertifikasını kontrol eder. İstemcinin ekran çıktısının biçimi',
        "  belgelenmemiştir; sonucu çıktıdan okuyun. Bu yöntem yalnızca Kamu SM damgalı günler",
        "  içindir. <KLASÖR>, jar dosyasını koyduğunuz klasördür. Paket klasöründen başlayarak:",
    ]
    for day in facts.days:
        lines.append(f"    # {format_date(day.day)}")
        lines += [f"    {command}" for command in zamane_commands(day)]
        lines.append(f"    cd {'/'.join(['..'] * len(day.folder.split('/')))}")
    lines += [
        "",
        "6. YÖNTEM C — OpenSSL ve özet araçlarıyla, verify.py olmadan",
        "-" * 59,
        "  Bu yöntem yalnızca OpenSSL'e (1.1 veya 3.x) ve standart özet araçlarına dayanır. Bütün",
        "  komutlar paket klasöründe çalıştırılır. Kökler için önce bölüm 7'yi okuyun.",
        "  C1) Her günün manifesti ve zaman damgası:",
    ]
    for day in facts.days:
        lines.append(f"    {openssl_command(day, facts.roots_bundle)}")
    lines += [
        '      Beklenen çıktı: "Verification: OK". -attime, sertifikaların zaman damgası anında',
        "      (genTime) geçerli olup olmadığına bakılmasını sağlar; TSA sertifikasının süresi",
        "      sonradan dolmuş olsa da doğrulama böylece yapılabilir. Bu adım manifestin",
        "      damgalandığı andan beri değişmediğini gösterir.",
        "  C2) Kayıt dosyaları: her dosyanın SHA-256 özeti, o günün manifestinde aynı dosyanın",
        '      ("files" listesi ve "audit_log") "sha256" değeriyle aynı olmalıdır.',
    ]
    if not facts.audit_log_included:
        lines.append(
            '      Denetim kaydı ("audit_log") bu pakette yoktur; bu bir eksiklik değildir.'
        )
    for day in facts.days:
        linux, powershell = hash_commands(day)
        lines += [f"    {linux}", f"    {powershell}"]
    lines.append(
        "      (cmd.exe: certutil -hashfile <dosya> SHA256). Manifestte olup klasörde olmayan ya da"
    )
    lines.append(
        "      klasörde olup manifestte olmayan bir dosya varsa paket eksik ya da eklenmiştir."
    )
    pairs = chain_commands(facts.days)
    if pairs:
        lines.append(
            "  C3) Günler arası zincir: önceki günün manifest ve damga dosyalarının özetleri,"
        )
        lines.append(
            '      sonraki günün manifestindeki "previous" bölümündeki değerlerle aynı olmalıdır:'
        )
        for day, command in pairs:
            lines += [f"    {command}", f'      -> manifest-{day.day.isoformat()}.json "previous"']
    if facts.extract_files:
        lines += [
            "  C4) Alıntı: extract dosyasındaki <N><TAB><satır> satırı, mühürlü dosyanın",
            "      açılmış halindeki N. satırla bayt bayt aynı olmalıdır (Linux, macOS ya da",
            "      Windows'ta Git Bash / WSL ile; N yerine satır numarasını yazın):",
        ]
        lines += [f"    {extract_command(name)}" for name in facts.extract_files]
    lines += [
        "",
        "7. KÖK SERTİFİKALAR VE PARMAK İZLERİ",
        "-" * 36,
        "  Zaman damgalarına güven, zincirin ulaştığı kök sertifikaya dayanır. verify.py ve",
        "  OpenSSL bu paketteki kökleri kullanır; bunların gerçekten Kamu SM'ye ait olduğunu",
        "  kendiniz kontrol etmelisiniz: aşağıdaki SHA-256 parmak izlerini Kamu SM'nin",
        "  sertifikalar.kamusm.gov.tr adresinde yayımladığı değerlerle karşılaştırın ya da kökleri",
        '  oradan kendiniz indirip "verify.py --kokler KLASÖR" ile verin.',
    ]
    for root in facts.roots:
        lines += [
            "",
            f"  {root.path}",
            f"    Konu       : {root.subject}",
            f"    Geçerlilik : {format_datetime(root.not_before)} - "
            f"{format_datetime(root.not_after)}",
            f"    SHA-256    : {colon(root.sha256)}",
            f"    SHA-1      : {colon(root.sha1)}",
        ]
    lines += ["", "8. GÜNLER", "-" * 9]
    for day in facts.days:
        grade = (
            ""
            if day.legal_grade
            else " — kökün parmak izini karşılaştırın (bölüm 7)"
            if day.legal_grade_claimed
            else " — TEST DAMGASI, yasal geçerliliği yok"
        )
        lines += [
            f"  {format_date(day.day)}: damga zamanı {format_datetime(day.gen_time)}, seri no "
            f"{day.serial}, sağlayıcı {day.provider}{grade}",
            f"    TSA: {day.tsa}",
            f"    {len(day.files)} kayıt dosyası pakette",
        ]
    return _encode(lines)


def render_english(facts: GuideFacts) -> bytes:
    first, last = _days_span(facts)
    test_days = [day.day.isoformat() for day in facts.days if not day.legal_grade_claimed]
    unconfirmed = [
        day.day.isoformat() for day in facts.days if day.legal_grade_claimed and not day.legal_grade
    ]
    lines = [
        "ARCILOGGER EVIDENCE PACKAGE - HOW TO VERIFY (short English version of DOGRULAMA.txt)",
        "=" * 86,
        "",
        f"Package  : {facts.package_id}",
        f"Created  : {format_datetime(facts.created_at)} (Turkey time, UTC+03:00) by "
        f"{facts.created_by or '-'}",
        f"Days     : {first} - {last} ({len(facts.days)})",
        f"Sources  : {', '.join(facts.sources) if facts.sources else 'all'}",
        f"Version  : ARCILOGGER {facts.version}",
        "",
        *textwrap.wrap(LEGAL_NOTE_EN, 92),
        "",
        "What it shows: each daily manifest existed at the time in its RFC 3161 time stamp",
        "(TÜBİTAK BİLGEM Kamu SM; only the SHA-256 hash was sent) and has not changed since; every",
        "sealed log file in the package matches the SHA-256 in its manifest; consecutive days are",
        "chained (each manifest holds the hashes of the previous day's manifest and token); the",
        "lines in extract/ equal the same-numbered lines of the sealed files byte for byte.",
        "What it does not show: who produced the logs or that devices logged correctly; that",
        "nothing changed before sealing; that an IP address belonged to a person; that the root",
        "certificates in the package are Kamu SM's (compare the fingerprints below with",
        "sertifikalar.kamusm.gov.tr). Revocation is not checked online.",
    ]
    if test_days:
        lines.append(
            "WARNING: these days carry a TEST time stamp without legal validity: "
            + ", ".join(test_days)
        )
    if unconfirmed:
        lines.append(
            "NOTE: these days are recorded as stamped by the Kamu SM production server, but the "
            "root they chain to is not listed as a Kamu SM root in ARCILOGGER: "
            + ", ".join(unconfirmed)
            + ". Compare its fingerprint with Kamu SM's published value."
        )
    lines += [
        "",
        "A) verify.py (Python 3.9+, standard library only, no network, writes nothing):",
        "     Windows: py -3 -I -S verify.py     Linux: python3 -I -S verify.py",
        '   Last line "SONUÇ: DOĞRULANDI [RESULT: VERIFIED]" means every check passed (read the',
        "   warnings too). --kokler DIR trusts only the roots in DIR (e.g. from Kamu SM's site).",
        "   verify.py comes inside this package, so whoever could change the package could change",
        "   it too. Trust its result only if you confirmed its SHA-256 through a channel separate",
        "   from the package, or ran your own copy (python -I -S <your copy>/verify.py <folder>);",
        "   otherwise use C, which does not depend on verify.py.",
        f"   SHA-256 of this package's verify.py: {facts.verifier_sha256 or '-'}",
    ]
    if facts.encrypted:
        lines.append(
            "   The ZIP is AES-256 encrypted: extract it with its password (e.g. with 7-Zip) first."
        )
    lines += [
        "B) Kamu SM's console client (Java 1.8), in each day folder:",
        f"     java -jar {ZAMANE_JAR} -CC manifest-YYYY-MM-DD.json manifest-YYYY-MM-DD.json.zd",
        f"   Download it from {ZAMANE_PAGE}",
        "   and compare its SHA-256 with the value on that page.",
        'C) Without verify.py, from the package folder. C1: OpenSSL ("Verification: OK"):',
    ]
    lines += [f"     {openssl_command(day, facts.roots_bundle)}" for day in facts.days]
    lines += [
        "   C2: the SHA-256 of every file of a day folder (sha256sum / Get-FileHash) must equal",
        "   its \"sha256\" in that day's manifest. C3: the SHA-256 of the previous day's manifest",
        '   and .zd must equal "previous" in the next manifest. C4: line N of an extract file',
        "   must equal line N of the decompressed sealed file (gzip -dc FILE | sed -n 'Np').",
    ]
    if not facts.audit_log_included:
        lines += [
            "   ARCILOGGER's own daily audit log (audit.jsonl.gz) is left out on purpose: it holds",
            "   other requests and staff data (KVKK data minimisation). The manifest lists it; its",
            '   SHA-256 is in paket.json ("excluded"). That is not a missing file.',
        ]
    if facts.report:
        lines += [
            "",
            f"Report: {facts.report} is ARCILOGGER's stored 5651 report, byte for byte.",
            "Its SHA-256 (in paket.json) must equal the value in ARCILOGGER's lookup record and",
            "audit log.",
        ]
    if facts.report_token:
        lines += [
            f"Its RFC 3161 time stamp {facts.report_token} is checked by verify.py, or with",
            f"     openssl ts -verify -in {facts.report_token} -data {facts.report} "
            f"-CAfile {facts.roots_bundle}",
        ]
    if facts.report_note:
        lines.append("The report's time stamp is not in the package:")
        lines += ["  " + part for part in textwrap.wrap(facts.report_note, 88)]
    lines += ["", "Root certificates (SHA-256 fingerprints):"]
    for root in facts.roots:
        lines += [f"  {root.path}", f"    {root.subject}", f"    SHA-256 {colon(root.sha256)}"]
    return _encode(lines)
