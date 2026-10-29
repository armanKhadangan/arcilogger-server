# Ingest servisi / Ingest service (`ArciIngest`, `arcilogger-ingest`)

> Durum: M2 (iş paketi B2). Kod: `ingest/src/arcilogger_ingest/`. Testler: `ingest/tests/`
> (gerçek PostgreSQL ile). Sözleşmeler: [MVP-CONTRACTS.md](MVP-CONTRACTS.md) bölüm 2–4.

## Özet (TR)

- Toplayıcı (`ArciCollector`) her mesajı önce ham dosyaya yazar:
  `D:\ARCILOGGER\spool\<kaynak>\<GGGG-AA-GG>.log`. **Ingest bu dosyaları kuyruk olarak okur**
  (tail), satırları ayrıştırır ve PostgreSQL'e toplu (`COPY`) yükler. Ham dosyaya hiçbir zaman
  yazmaz, dosyayı değiştirmez, silmez.
- Her (kaynak, gün) için **kaldığı yer** (`ingest_offsets`: bayt konumu + satır numarası)
  yüklenen satırlarla **aynı veritabanı işleminde** kaydedilir. Bu yüzden servis çökse, elektrik
  kesilse veya süreç öldürülse bile bir satır **ne iki kez yüklenir ne de atlanır**.
- Arama'nın özet tablosu `search_counts` (saat, ham dosya, kayıt türü, tablo, eylem ve özel /24
  ağı başına **kesin** satır sayıları) da satırlarla **aynı işlemde** yazılır: yüklenen her satır
  tam bir kez sayılır. Bu sürüme yükseltmeden önce yüklenmiş dosyaları worker işi
  `search.counts_backfill` gün bitince sayar; o zamana kadar o günlerin istatistikleri doğrudan
  sayılır. Bu adımın kendi maliyeti tek süreçli yüklemenin işlemci süresinin yaklaşık %1,4'üdür;
  `bench_throughput.py --repeat 3` ile Wave 1'e göre yavaşlama ölçülemedi (medyanlar +%0,2 ve
  2 worker ile +%1,6). Bu ölçümde web filtresi satırı yoktur; %18'i web filtresi olan yük
  üreteci gününde iki karşılaştırmalı ölçümün medyanı Wave 1'den %1,9 (tek süreç) ve %3,8
  (2 worker) yavaştır: %5 sınırının içinde, ama sıfır değil (aşağıda "Measured throughput").
- FortiGate DHCP satırları (`fg_dhcp`) ve oturum kayıtlarındaki `srcmac` (ADR-0016) yüklemeyi
  %5 sınırının ötesinde yavaşlatmaz: aynı satırlar iki sürüme de yüklenip karşılaştırıldı
  (`bench_throughput.py --repeat 3`, sırayla 12'şer çalıştırma). Varsayılan karışımda fark tek
  süreçte +%0,8, 2 worker ile +%0,7'dir (yeni kod yolu çalışmaz). Yalnızca FortiGate'ten gelen
  karışımda (%70 `srcmac`'li oturum, %30 DHCP) değişikliğin bir maliyeti vardır: tek süreçte
  −%3,5 (işlemci süresi +%3,9), 2 worker ile +%1,7 (aşağıda "Measured throughput").
  Gerçekçi yük üreteci günlerinde (varsayılan seçenekler ve `--dhcp-server fortigate --srcmac`;
  her gün bir kez yazılıp iki sürüme de yüklendi, sırayla 16'şar çalıştırma) fark tek süreçte
  −%3,1 / −%2,4, 2 worker ile −%2,8 / −%3,2'dir: %5 sınırının içinde, ama sıfır değil.
- Veritabanı kapalıysa servis bekler ve artan aralıklarla (en çok 30 sn) yeniden dener; ham
  dosyalar kuyruk olduğu için **hiçbir şey kaybolmaz**. Veritabanı dönünce kendiliğinden devam
  eder.
- Gece mühürleme, kapanmış günün ham dosyasını arşive (vault) sıkıştırıp doğruladıktan sonra
  **siler**. Ingest o güne yetişememişse aynı bayt konumundan **mühürlü arşiv kopyasından**
  devam eder. Gün mühürlenmiş ve tamamen yüklenmişse "bitti" olarak işaretlenir.
- Durum dosyası `D:\ARCILOGGER\state\ingest-status.json` (kaynak başına gün, konum, gecikme,
  satır/sn, son hata) gösterge paneli ve Sistem sayfası içindir.
- Bozuk bir ham satır (ör. elektrik kesintisi sonrası dosya sistemi hasarı) veya ayrıştırıcı
  hatası **yalnızca o satırı** etkiler: `parse_failures` tablosunda sayılır, servis durmaz.
  Toplayıcı, yarım kalmış son satırı (çökme, elektrik kesintisi, dolu disk) bir sonraki
  mesajdan önce sonuna yalnızca `\n` ekleyerek kapatır (mevcut baytlar değişmez); yarım satır
  böylece tek bir bozuk satır olarak sayılır ve sonraki sağlam mesajı yutmaz.
- Aynı veritabanında **tek bir ingest** çalışır (PostgreSQL danışma kilidi). Servis çalışırken
  elle başlatılan ikinci bir `arcilogger-ingest run` bekler ve durum dosyasına yazmaz;
  `--once` ile çıkış kodu 4 verir.
- Ölçülen hız (geliştirme sanal makinesi, 4 vCPU, aynı makinede PostgreSQL 16, gerçek
  FortiGate/MikroTik ayrıştırıcıları, tekrarlanan ölçümler): tek süreçte **saniyede ~4.700–5.300
  satır**, varsayılan 2 ayrıştırma süreciyle (`workers = 2`) **~7.400–10.500 satır** (gerçekçi
  yük üreteci karışımında ~7.200–7.900). Şartnamenin (14) 4 çekirdekte **saniyede ≥ 10.000
  mesaj sürekli** hedefi gerçek ayrıştırıcılarla **gösterilmedi**: saniyede 10.000 satırlık 30
  saniyelik canlı akışa yetişemedi (gecikme ~5 sn ve büyüyordu). Gerçek ortalama hacim çok daha
  düşüktür (saniyede onlarca mesaj); birikmiş kayıtların yüklenmesi ise beklenenden ~%30 yavaş
  sürer. Sunucuda yeniden ölçülmesi bir kabul maddesidir (aşağıda).
- Bir ayrıştırma süreci çökerse (virüs tarayıcı, bellek, dışarıdan sonlandırma) satırlar servis
  sürecinde ayrıştırılmaya devam eder ve havuz kısa bir beklemeden sonra **yeniden kurulur**;
  `ingest-status.json` içindeki `parse_workers` yapılandırılan ve etkin süreç sayısını, yeniden
  başlatmaları ve son hatayı gösterir.

## How it works (EN)

```
spool/<source>/<day>.log ──► read complete lines ──► prepare (rawlog → parser → check_row)
   (or vault/…/<source>.log.gz                          │   in-process, or N worker processes
    once the sealer deleted the spool file)             ▼
                                         one transaction: offsets (compare-and-set) → COPY rows
                                         → parse_failures upsert → sources (+ new_source alert)
                                         → search_counts upsert → COMMIT → in-memory offsets move
```

| Module | Role |
|---|---|
| `service.py` | the loop: discover streams, read, batch, load, finish, back off, status |
| `files.py` | `read_complete` (spool, opened and closed per read), `VaultReader` (sealed `.log.gz`), `open_shared` (Windows delete sharing) |
| `prepare.py` | one raw line → rows (pure function; runs in a worker process unchanged) |
| `loader.py` | the load transaction (rows, failures, sources, search counts), compare-and-set offsets, bisecting a batch `COPY` refuses |
| `instance.py` | single-instance lock (PostgreSQL session-level advisory lock) |
| `sealed.py` | which days the sealer finished (`state/seal/<day>.json` + manifest) |
| `sources.py` | spool folder → configured type (`fortigate`, `ap-<ip>` → network entry, `unknown-<ip>` → content detection) |
| `pool.py` | optional parse worker processes (`--workers N`) |
| `status.py` | `state/ingest-status.json` |
| `stopping.py` | Ctrl+C / Ctrl+Break / console close / shutdown / SIGTERM → graceful stop |

**Streams.** One per `(source, day)`: every spool file (listed every poll), every unfinished
`ingest_offsets` row, and every source of a **sealed** day within the database retention
(`db.retention_days`, default 400) that is not finished — the vault scan (once a minute) is how a
day whose spool file was deleted before ingest saw it (service down over a weekend) is still
loaded. Sealed days older than the retention are not loaded automatically (that is the later
"arşivden yeniden yükle" feature). Newest day first, so live data is not delayed by a backlog.
The sources of one day **share** each batch in rounds (equal shares; what one source cannot use
goes to the others), so a large backlog of one source — DHCP or access points after an outage —
does not hold back `fortigate`, which sorts after `ap-*` and `core-r1` and matters most for 5651
lookups.

**Lines.** Only complete (`\n`-terminated) lines are read; a line still being written is left
for the next poll. `raw_offset` is the byte offset of the line start in the uncompressed file,
`raw_line` its 1-based number (tested against the files, including after the switch to the
vault copy). A sealed file is final, so its last line counts even without `\n`.

**Rows.** `arcilogger_ingest.parsers.parse_line` (registry only), then `check_row(…,
complete=True)` on every row:

| Case | Result |
|---|---|
| line not in the collector's format (e.g. zeros after file-system damage; a sender field that is not ASCII; a receive time without a UTC equivalent such as year 1 at +03:00) | parse failure `rawlog.invalid`, **no** `messages` row (receive time and sender would have to be invented) |
| parser reports an error | parse failure `<parser pattern>`; rows as parsed |
| typed row fails `check_row` | row dropped, parse failure `rowcheck.<table>`, `messages.parse_error` set |
| `messages` row fails `check_row` | the generic view of the line is loaded instead (`parse_error = rowcheck.messages`) |
| the generic view fails too — the sender is not storable as `INET` (e.g. a link-local IPv6 sender with a zone id, `fe80::1%12`, as Windows reports it) | parse failure `rowcheck.messages`, **no** `messages` row (*verify on site* whether such senders occur) |
| the parser or a row check **raises** (a bug the parser's own guard misses) | parse failure `prepare.exception` (exception type in `error`), generic `messages` row with that `parse_error`; logged at ERROR once per (source, day) |
| row passes `check_row` but `COPY` refuses it (should not happen) | the batch is bisected in savepoints; only that line is counted (`copy.<table>`); its `messages` row is kept when possible |

Nothing in preparing a line raises for any bytes (a hypothesis test feeds damaged lines): a
line that crashed the service would crash it again after every restart and stop ingest for
every source, and the raw line is evidence that is never edited. Rows of one table are loaded
with one `COPY` per column set, so a column a row leaves out (`fg_traffic.is_interim`, `extra`)
gets its server default. If `COPY` refuses a batch although every line then loads on its own,
a WARNING says so (the bisection is slow and would otherwise only show as lag).

Failures are aggregated per `(source, day, pattern)` with the contract's upsert (`count =
parse_failures.count + EXCLUDED.count`, first example kept).

**Sources.** Configured exact sources get a `sources` row at start-up (type, address,
`expected`, heartbeat from the TOML). Every batch updates `first_seen`/`last_seen`/address. An
`unknown-<ip>` sender gets `unknown = true`, the detected type, and **one** `new_source` alert
(`dedup_key = "new_source:<name>"`) when its row is first created — resolving the alert does not
make it come back. (The collector also writes `new-log-source` to `state/alerts.jsonl`; the
worker that imports that file should use the same key.)

## Exactly once

1. The new offsets are written **in the same transaction** as the rows they cover.
2. The offset update is a **compare-and-set** (`WHERE byte_offset = <where this batch started>
   AND line_number = … AND finished_at IS NULL`), done first so it also takes the row lock. If
   anything else moved the offset (a second instance started by mistake), the batch is refused
   and the offsets are reloaded — never loaded twice.
3. In-memory offsets move only after `COMMIT`; after a restart they come only from the database.

Tests: an interruption at every step of the transaction (`offsets`, `copy`, `failures`,
`sources`, `counts`, `commit`), a second instance moving the offsets mid-batch, two instances
racing on the same files (lock switched off), the real process killed three times with
`SIGKILL`/`TerminateProcess` during loading, re-runs over the same files, and database outages
(a TCP proxy that drops every connection): before the `COPY`, **while `COPY` is streaming rows**,
**as `COMMIT` is sent**, and a `COMMIT` that **succeeded but whose answer was lost** (the retry
finds the offsets already moved and continues from the database) — every time each line is in
`messages` exactly once and rows and offsets agree; after the interruptions, the second instance,
the race and the kills the search counts agree with them too (`searchcounts.verify` in the
same snapshot).

## Search counts

The step `counts` writes the summary table `search_counts` of Arama's statistics
([ADR-0015](adr/0015-search-live-sources.md), `arcilogger_db.searchcounts`) in the load
transaction, from exactly the lines whose rows loaded in it:

- One key per hour of the device time (`messages.time`, UTC), spool file (`source`, `raw_day`),
  kind of the `messages` row (`kinds.kind_of`), typed table (`''` for none), the typed row's
  `action` (lower-cased) and the /24 of its client address when that is a private IPv4 address;
  `count_key` is the one definition, also used by `rebuild_stream`, so what ingest counted equals
  a recount of the rows (tested on every path below). The loader groups a batch's lines by what
  `count_key` reads and makes each key once per group (about 1.8 µs a line instead of 5); until
  the step it keeps the runs of lines `COPY` loaded, not one object per line.
- A line whose typed row is not loaded — dropped by the row check (`rowcheck.<table>`) or refused
  by `COPY` and salvaged with its `messages` row alone (`copy.<table>`) — counts with `tbl = ''`:
  the search over the typed tables does not find it either. A line without a `messages` row
  (`rawlog.invalid`, `copy.messages`, a `rowcheck.messages` without generic view) is not
  counted: it is in neither search nor statistics, only in `parse_failures`.
- One fixed statement per 500 keys, `INSERT … SELECT FROM unnest(:hours, :sources, …) ON
  CONFLICT … DO UPDATE SET n = search_counts.n + EXCLUDED.n`, the keys as parallel arrays in
  sorted order (a multi-row `INSERT` built and compiled per batch cost 7.2–7.3 ms of the service
  process's CPU for 57 keys; the cached statement 1.0–1.1 ms, wall 8.7–8.9 → 2.0–2.5 ms). A batch of 5,000 lines has tens to a few hundred keys; the load-generator day
  below (217,875 lines) has 800. No lock of its own: one ingest writes a file (instance lock and
  compare-and-set), and a finished file is never written again.
- **Files loaded before revision `0016_search`.** The migration sets
  `ingest_offsets.counts_from_line = line_number + 1` on every file with loaded lines. Ingest
  counts what it loads from then on and leaves the mark; the worker job `search.counts_backfill`
  (every 10 minutes, no new file after 60 s, newest day first; [CORRELATION.md](CORRELATION.md))
  recounts a file once it is finished and clears the mark. Until then the statistics of the days
  around that file are counted directly (slower, same result). `arcilogger-db search-counts
  status` lists the files still to count, `rebuild --source S --day YYYY-MM-DD` recounts one at
  once (also one still being loaded: it locks the file's `ingest_offsets` row, so ingest's next
  batch of that file waits until the recount commits), `verify --hours 2` recounts the recent
  hours from the rows and exits 1 on a difference.

Tests: `ingest/tests/test_loader_counts.py` (shared batches over three sources and two days, a
damaged line, a dropped typed row, the `COPY` salvage, vault continuation, a file half loaded
before the upgrade, a device clock almost a day behind) and the exactly-once matrix above.

## One instance per database

The service holds a PostgreSQL **session-level advisory lock** (`instance.py`) on a connection
of its own, for as long as it runs. A second instance — e.g. `arcilogger-ingest run` typed in a
console while the `ArciIngest` service runs — logs "another ingest instance is running", loads
nothing, writes **no** status file (the file belongs to the running one) and checks every 5 s;
when the first stops it takes over. With `--once` it exits with code 4. The lock disappears with
its database session, so a killed or crashed service never leaves a stale lock. After a database
restart the lock is taken again before the next batch; if another instance got it in between,
this one drops what it had read and waits. The compare-and-set above stays as the safety net.

## Windows: file sharing and the sealer

The sealer deletes a closed day's spool files after verifying the vault copy. On Windows a file
cannot be deleted while another process has it open without `FILE_SHARE_DELETE`, and Python's
`open()` does not ask for it. Ingest therefore

- **never keeps a spool file open between polls** (open, read the new bytes, close), and
- opens files with `CreateFileW(…, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, …)`
  (`files.open_shared`), so even a delete *during* a read succeeds (the open handle stays valid).

Without that, the sealer's `unlink` would fail with "access denied", the day would stay pending
with a "Mühürleme başarısız" alert and be retried minutes later. A Windows-only test documents
the plain-`open()` behaviour; the shared open is tested on every platform (*verify on the
server*: run `uv run pytest ingest/tests/test_files.py` there).

**Vault continuation.** When the spool file is gone, ingest reads
`vault/<Y>/<M>/<D>/<source>.log.gz` (decompressed byte offsets and line numbers are identical,
the sealer compresses byte for byte), skips to the committed offset and continues. A stream is
**finished** (`ingest_offsets.finished_at`) when the day's seal state is `sealed` and the
committed offset equals `bytes_uncompressed` of the manifest. Not finished, and reported in the
status file: spool missing and day not sealed (also no vault copy), source missing from the
manifest, damaged vault file (retried every poll), more bytes loaded than sealed. In the sealer's
own crash window (spool deleted, state not yet `sealed`) a known stream continues from the
verified vault copy and is finished once the state says `sealed`.

## Service, stop, exit codes

```
arcilogger-ingest run [--config PATH] [--once] [--workers N]
arcilogger-ingest status [--config PATH]
```

| Exit | Meaning |
|---|---|
| 0 | stopped on request; with `--once`: everything readable was loaded |
| 1 | unexpected internal error (logged with traceback; the wrapper restarts the service) |
| 2 | configuration error (TOML unreadable/invalid, secret store key missing/wrong) |
| 3 | `--once` only: database unreachable (the service mode waits instead) |
| 4 | `--once` only: another ingest instance is running on the same database (the service mode waits instead) |

Stop: Ctrl+C, Ctrl+Break (what service wrappers send), SIGTERM, console close and system
shutdown. The current batch is loaded (or, with the database down, dropped — it is read again
at the next start), the status file is written, exit 0. For console close/shutdown Windows ends
the process when the handler returns, so the handler waits up to 4.5 s for the loop.
`CTRL_LOGOFF_EVENT` is ignored: a service receives it whenever any user logs off. Logging goes
to the console (stderr) for the service wrapper.

`[ingest]` in `arcilogger.toml`: `batch_rows` (5000), `batch_seconds` (0.5), `poll_seconds`
(0.5), `workers` (2; 0–16, 0 = parse in the service process; `run --workers N` overrides it). A
batch is loaded when it has `batch_rows` lines or is `batch_seconds` old; reads are sized by each
stream's average line length so a batch holds at most `batch_rows` lines.

## Status file

`state/ingest-status.json`, written atomically every 5 s and on stop, only by the instance
holding the lock:
`state` (`starting`, `running`, `database_unavailable`, `stopping`, `stopped`, `failed` = ended
by an unexpected error, exit code 1),
`database.ok/last_error`, `parse_workers` (`configured`, `active` — 0 while the chunks are
parsed in the service process until a broken pool is replaced —, `restarts`, `last_error`),
totals (lines, rows, parse failures, batches, lines/s) and per source:
newest unfinished `day`, committed `byte_offset`/`line_number`, `read_from` (`spool`|`vault`),
`lag_bytes` (all unfinished days), `lag_seconds` (now − receive time of the last loaded line,
0 when caught up, `null` when unknown), `lines_per_second`, `last_received`,
`unfinished_days`, `last_error`. Rates and lags are estimates.

## Measured throughput

`ingest/tests/bench_throughput.py` (end to end: spool files written with
`rawlog.format_line` → PostgreSQL 16 on the same machine, 70 % FortiGate traffic lines of the
format samples / 30 % synthetic MikroTik DHCP lines, average 610 bytes). Development machine: 4
vCPU, 16 GB, Linux, plain PostgreSQL (native partitions, no TimescaleDB). The rows above the real-parser
rows were measured with the stub parsers (generic view, `messages` rows only) and are kept for
history. **The speed of the VM varies by about 20 % between runs**, and runs on a quiet machine
were not faster than the single runs below: report the minimum and median of
`--repeat 3` (or more) with the load average, never one fast run.

| Run (2026-10-12) | Result |
|---|---|
| backlog 200,000 lines, `batch_rows` 1,000 / 5,000 / 20,000, in-process | 12,900 / 14,800 / 14,800 lines/s; ingest process 85–92 % of one core |
| same, `--workers 2` | 20,600 / 17,700 / 16,800 lines/s; ingest process 36–54 % of one core, machine 77–97 % |
| live feed **10,000 lines/s for 30 s**, in-process | kept up: largest backlog 10,770 lines (≈ 1.1 s = poll + batch interval), caught up 0.6 s after the feed stopped |
| live feed 10,000 lines/s, `--workers 2` | kept up: largest backlog ≈ 1.6 s, caught up 0.2 s after |
| live feed 15,000 lines/s, in-process / `--workers 2` | kept up (backlog ≈ 1.6 / 1.8 s) |
| after the review fixes (shared batches, lock check per batch, `COPY` per column set): backlog 200,000, `batch_rows` 5,000, in-process / `--workers 2` | 13,630 / 22,600 lines/s (ingest process 89 % / 45 % of one core) |
| same code, live feed 10,000 lines/s for 20 s, in-process / `--workers 2` | kept up: largest backlog ≈ 1.13 / 1.49 s, caught up 0.56 / 0.32 s after |
| **with the real FortiGate/MikroTik parsers** (merged 2026-10-12; every line also gives a typed row): backlog 100,000 lines, `batch_rows` 5,000, in-process / `--workers 2` | 5,282 / 10,525 lines/s (one run each; ingest process 94 % / 49 % of one core, machine 30 % / 67 % of all 4 cores) |
| final review (2026-10-13), same command, repeated runs, quiet and shared machine: in-process / `--workers 2` / `--workers 3` | **4,658–5,300 / 7,447–9,385 / 9,594–11,174 lines/s** (ingest process ≈ 48 % of one core with 2 workers; machine 69–72 %) — the commit that gave 10,525 measured 8,325–8,648 on the same VM: the earlier figure was one fast run, not a regression |
| realistic load-generator mix (`tools/loadgen`, 2 days, 431,000–441,000 lines: 35 % DNS, 27 % traffic, 18 % web filter, 17 % application control), workers 0 / 2 / 3 | **3,755–3,809 / 7,191–7,941 / 7,477–8,729 lines/s** |
| live feed **10,000 lines/s for 30 s**, real parsers, `--workers 2` | **did not keep up**: backlog 52,285 lines (≈ 5.2 s) and growing; caught up 5.7 s after the feed stopped |

**Wave 2: the `counts` step and the `fg_web` index (2026-10-26).** The gate of the Wave 2
contract: `bench_throughput.py --repeat 3` before (Wave 1) and after, at most 5 % slower. The same
VM, `--lines 200000 --batch-rows 5000 --workers 0 2 --repeat 3`, the two trees alternating
(Wave 1, Wave 2, Wave 2, Wave 1, twice: 12 runs each; load average 1.3–2.8); min–max (median)
lines/s:

| `bench_throughput.py --repeat 3`, 12 runs each | in-process | `--workers 2` |
|---|---|---|
| Wave 1 (no `fg_web` index, no `counts` step) | 5,713–6,345 (6,156) | 9,668–12,368 (11,680) |
| **Wave 2 (the index and the `counts` step)** | **5,657–6,407 (6,171)** | **11,513–12,346 (11,866)** |

The medians differ by +0.2 % and +1.6 %: no slowdown is measurable, the gate as the contract
sets it holds (ingest process CPU, median: 30.98 / 30.99 s in-process, 8.68 / 8.82 s with 2
workers). This benchmark loads **no web filter line** (its FortiGate format samples have none,
the rest is DHCP): it measures the `counts` step with few keys a batch and not the `fg_web`
index at all; both are measured on the load-generator day below. The step's own
cost, measured inside such a run: 1.8 µs a line to make the keys (5 µs before the lines were
grouped by what `count_key` reads) and 0.1 s CPU for the 40 upserts (2–7 keys each), together
0.45 s of the 31 s an in-process run takes (1.4 %); with 2 workers the service process, at about
50 % of one core, has that time spare. A first version kept a tuple per loaded line until the
step: the garbage collector then ran about 300 times more per 200,000 lines (0.3 s), and single
runs alternating with Wave 1 on a busy machine were 3.5–4.6 % slower in-process; the loader now
keeps the runs of lines `COPY` loaded.

**The load-generator day (2026-10-27).** One site day of `tools/loadgen` (217,875 lines: 35 % DNS,
27 % traffic, 18 % web filter, 17 % application control; 800 summary keys, about 57 a batch),
loaded into a fresh database per run (`run(once=True)`, `batch_rows` 5,000), 2 runs per tree and
worker count in each turn, the trees alternating in two campaigns: Wave 1, Wave 2, Wave 2, Wave 1,
twice; then Wave 1, the `fg_web` index alone (the tree before the `counts` step), Wave 2, Wave 2,
the index alone, Wave 1, twice. 8 runs per tree and campaign, load average 0.1–4.2 (another
agent's work shared the machine); min–max (median) lines/s, the difference of the medians to
Wave 1 of the same campaign:

| Load-generator day | in-process | `--workers 2` |
|---|---|---|
| 1st campaign: Wave 1 | 5,095–5,711 (5,429) | 9,322–11,924 (10,921) |
| 1st campaign: **Wave 2** | **4,919–5,415 (5,130), −5.5 %** | **8,516–11,062 (10,192), −6.7 %** |
| 2nd campaign: Wave 1 | 5,303–5,760 (5,538) | 10,144–11,612 (10,770) |
| 2nd campaign: the `fg_web` index alone | 5,312–5,783 (5,667), +2.3 % | 8,388–11,368 (10,542), −2.1 % |
| 2nd campaign: **Wave 2** | **5,343–5,596 (5,462), −1.4 %** | **10,213–11,487 (10,746), −0.2 %** |
| both campaigns, 16 runs each: Wave 1 / **Wave 2** | 5,471 / **5,366, −1.9 %** | 10,921 / **10,506, −3.8 %** |

On the realistic mix Wave 2 is slower, but over both campaigns within the 5 % of the gate
(−1.9 % in-process, −3.8 % with 2 workers); one campaign of 8 runs ranges from −0.2 % to
−6.7 % and cannot tell 5 % apart on this machine. The index alone shows no cost beyond that noise
(+2.3 %, −2.1 %). The step's own time in such a run, instrumented (2 runs per worker count):
2.0–2.4 µs a line to make the keys and 0.42–0.71 s for the 44 upserts (2,529 keys; about 5 ms of
each is SQLAlchemy building the multi-row `INSERT`), together 0.9 s, 2.2 % of an in-process run,
and 1.1–1.2 s, 5–6 % of a run with 2 workers: there it runs in the service process, at about
50–60 % of one core, so it is partly hidden. Re-measure on site with the real mix (below).

**After the cached upsert (2026-10-27).** `upsert_counts` now sends one fixed `unnest`
statement instead of building a multi-row `INSERT` per batch (above). The same load-generator
day, ABBA twice, 8 runs per tree and worker count (load average 2.0–4.0, another agent's work
sharing the machine); min–max (median) lines/s:

| Load-generator day | in-process | `--workers 2` |
|---|---|---|
| Wave 2 before the change / after it | 5,220–5,742 (5,562) / 4,930–5,757 (5,548), −0.3 % | 9,635–10,674 (9,887) / 9,219–10,384 (9,886), −0.0 % |
| Wave 1 / Wave 2 after the change | 5,026–5,520 (5,340) / 4,781–5,466 (5,132), −3.9 % | 9,043–10,428 (9,654) / 9,177–10,396 (9,622), −0.3 % |

The change saves CPU in the service process but no throughput measurable on this machine.
**The 5 % gate is met on average, but not shown with margin:** with 2 workers (the default) one
8-run campaign ranged −0.2 % to −6.7 % before the change and −0.3 % after it, in-process −1.4 %
to −5.5 % and now −3.9 %; this shared 4-vCPU VM cannot tell such differences apart. It stays an
on-site acceptance item with the real mix (`bench_throughput.py --repeat 3` and a day of the
site's own spool, Wave 1 against Wave 2).

Earlier rounds, while the test runs of the other Wave 2 packages shared the machine (load average
2–9, 30–100 % busy): one run per tree and round, and one load-generator day of 217,875 lines
(18 % web filter lines, 800 summary keys) besides; min–max (median) lines/s:

| Trees compared | `bench_throughput.py` in-process / `--workers 2` | load-generator day in-process / `--workers 2` |
|---|---|---|
| Wave 1 (no index, no counts), 3 rounds | 4,445–4,997 (4,993) / 6,416–8,670 (8,162) | 3,953–4,370 (4,348) / 5,226–8,348 (7,662) |
| with the `fg_web` index, same rounds (6 / 3) | 4,400–5,091 (4,815) / 6,506–9,904 (8,204) | 4,008–4,535 (4,332) / 6,392–8,412 (7,153) |
| with the index, 4 later rounds | 4,153–5,298 (4,856) / 5,225–7,136 (6,316) | 4,000–4,892 (4,544) / 6,216–7,917 (6,654) |
| with the index and the first version of the `counts` step, the same 4 rounds | 4,486–5,614 (4,997) / 4,329–8,673 (7,278) | 4,090–4,718 (4,372) / 5,777–6,734 (6,142) |

The index costs nothing measurable (the benchmark has no web filter lines; the load-generator day
has 18 %). The differences of these rounds (−8 % to +15 %) lie inside a spread of up to 27 %
between two runs of one round. The upsert of the load-generator day, with its 37–247 keys a
batch, took 15 ms a batch measured alone with a commit of its own (3 µs a line; in the load
transaction there is no commit of its own). Re-measure on site with the rest (below).

**Wave 3: `fg_dhcp`, `fg_traffic.srcmac` and the `mac` field kind (2026-10-28, ADR-0016).** The
gates 1–2 of the Wave 3 contract (3.8): `bench_throughput.py --repeat 3`, the Wave 2 tree
(`c5f14a4`) against the Wave 3 branch, at most 5 % slower. Method: each mix was written **once**
with the Wave 3 tool (`bench_throughput.py --write-spool DIR --lines 200000 --mix default`, and
`--mix fortigate-only`: 70 % forward traffic lines with `srcmac`/`mastersrcmac`, 30 % synthetic
FortiGate DHCP server lines, 9 in 10 ACKs and 1 in 10 RELEASEs), then loaded by both trees with
`--spool DIR --batch-rows 5000 --workers 0 2 --repeat 3`. The same `bench_throughput.py` was
copied unchanged into a worktree of the Wave 2 tree and run there with `PYTHONPATH` set to its
`core/src`, `db/src` and `ingest/src` (each tree migrates its own throwaway database; the Wave 2
tree stores the FortiGate DHCP lines in `fg_event` and `srcmac` in `extra`, the Wave 3 tree in
`fg_dhcp` and the column; 400,000 rows a run in both). Both trees load exactly the same bytes.
Order: Wave 2, Wave 3, Wave 3, Wave 2, twice per mix (ABBA ×2, 12 runs per tree and worker
count), gate 1 first, then gate 2, nothing else running (load average 2.4–2.8, which is this
benchmark itself: PostgreSQL and the ingest process). This 4-vCPU VM is about 45–50 % slower than
the one of the Wave 2 rows above; only the comparison within one table counts. Min–max (median)
lines/s, the difference of the medians to Wave 2:

| `bench_throughput.py --repeat 3`, 12 runs each | in-process | `--workers 2` |
|---|---|---|
| Gate 1, the default mix: Wave 2 | 3,262–3,421 (3,324) | 5,333–6,265 (5,798) |
| Gate 1, the default mix: **Wave 3** | **3,157–3,468 (3,351), +0.8 %** | **5,521–6,249 (5,839), +0.7 %** |
| Gate 2, the FortiGate-only spool: Wave 2 | 2,911–3,123 (3,031) | 4,622–5,594 (5,134) |
| Gate 2, the FortiGate-only spool: **Wave 3** | **2,841–3,075 (2,926), −3.5 %** | **4,652–5,654 (5,222), +1.7 %** |

Both gates hold. Gate 1's lines reach only the routing rule's key check, as expected: its
`event` lines (10 %) carry no `dhcp_msg`, and its traffic lines have no `srcmac` (ingest process
CPU, median: 55.2 / 55.4 s in-process). Gate 2 is the case the change costs something in: every
FortiGate DHCP line now converts a MAC and an address and is written to `fg_dhcp` instead of
`fg_event`, and every traffic line converts `srcmac` (ingest process CPU, median: 60.4 / 62.8 s
in-process, +3.9 %; with 2 workers the parsing runs in the workers and the medians show no cost).
The per-campaign
medians (one ABBA round each, 6 runs per tree): gate 1 −0.8 % / +1.8 % in-process and +6.3 % /
−2.4 % with 2 workers; gate 2 −2.0 % / −3.3 % in-process and −1.2 % / −0.7 % with 2 workers. The
machine got slower during gate 2's second campaign (both trees, about 3.5–5 % in-process and
10 % with 2 workers), which the alternation evens out. An earlier, unrecorded measurement of the
handed-over branch gave −5.5 % once under another job's load, and −2.2 % to −3.1 % (gate 1) and
−0.5 % to −3.1 % (gate 2) in quiet runs, in line with these. Gate 3 (the load-generator days,
`--dhcp-server fortigate --srcmac`) is measured by the integrator after WP-F1 and recorded below.

**Wave 3, gate 3: the load-generator days (2026-10-29).** Gate 3 of the Wave 3 contract (3.8): the
load-generator day with the default options and a `--dhcp-server fortigate --srcmac` day, each
written once and loaded by both trees through the same harness, at least 16 runs per tree
alternating, at most 5 % slower than Wave 2. Both days were written **once** with the Wave 3
`tools/loadgen`: `--day 2026-09-23 spool --data-dir DIR` (the day of the Wave 2 rows above: 217,875
lines, 138.7 MB, in `fortigate`, `core-r1` and `switch-1`) and `--day 2026-09-23 --dhcp-server
fortigate --srcmac spool --data-dir DIR` (214,553 lines, 140.2 MB, all from the FortiGate: as many
DNS, web filter, application control and traffic lines (58,973), the traffic lines now with
`srcmac`, and 242 DHCP ACK and 2 RELEASE lines, no MikroTik line). Both trees loaded them with
`bench_throughput.py --spool DIR/spool --batch-rows 5000 --workers 0 2`, one run per worker count
and invocation. The Wave 2 tree is `c5f14a4` with the method of gates 1–2 (the same
`bench_throughput.py` copied unchanged into its worktree, `PYTHONPATH` set to its `core/src`,
`db/src` and `ingest/src`, the same Python environment); the Wave 3 tree is `wave-3` at `fcd2c1d`
(with the branches then in review merged in, which change nothing under `core/src`, `db/src` or
`ingest/src`). `switch-1` is not a source of the harness's configuration: both trees parse its lines
by content. Both trees load exactly the same bytes and write the same number of rows (435,368 and
429,106 a run). Order: Wave 2, Wave 3, Wave 3, Wave 2, eight times per day (ABBA ×8, run by run: 16
runs per tree, day and worker count; two campaigns of ABBA ×4), the default day first. This
4-vCPU VM (Xeon 2.10 GHz, 16 GB, PostgreSQL 16 without TimescaleDB) was shared at times with other
work, including this integration's own browser tests and live smoke (about 16:44–16:48 UTC): load
average at the start of an invocation 1.8–2.6, except 2.7–5.9 for 7 invocations (14 runs) in the
middle of the default day (16:44–16:53 UTC, the end of its first campaign and the start of its
second), among them each tree's lowest run with 2 workers (5,503 and 6,234 lines/s). Min–max
(median) lines/s, the difference of the medians to Wave 2:

| `bench_throughput.py --spool`, 16 runs each | in-process | `--workers 2` |
|---|---|---|
| Gate 3, the load-generator day (default options): Wave 2 | 4,178–5,280 (4,997) | 5,503–9,551 (8,603) |
| Gate 3, the load-generator day (default options): **Wave 3** | **4,471–5,055 (4,843), −3.1 %** | **6,234–9,031 (8,361), −2.8 %** |
| Gate 3, `--dhcp-server fortigate --srcmac`: Wave 2 | 4,739–5,372 (5,053) | 7,787–10,118 (8,953) |
| Gate 3, `--dhcp-server fortigate --srcmac`: **Wave 3** | **4,644–5,265 (4,932), −2.4 %** | **7,720–9,726 (8,664), −3.2 %** |

The gate holds on both days: Wave 3 is 2.4–3.2 % slower in all four medians (ingest process CPU,
median, in-process: 39.2 / 40.4 s on the default day, +3.2 %; 38.3 / 39.4 s on the FortiGate day,
+2.8 %). On the default day no line takes the new paths: each traffic line only looks for `srcmac`
(`parsers/fortigate.py:133`, `:480`) and each event line for `dhcp_msg` (`:586`); this measurement
does not show where the extra CPU goes. The per-campaign medians (ABBA ×4, 8 runs per tree):
default day −3.0 % / −2.8 % in-process and −0.8 % / −3.9 % with 2 workers; FortiGate day −0.5 % /
+0.5 % in-process and −2.8 % / −6.8 % with 2 workers (in that last campaign both trees ran faster
than in the first, Wave 2 by 12 %, Wave 3 by 7 %). As on the Wave 2 day, one 8-run campaign cannot
tell 5 % apart on this VM; the 16-run medians are the measurement. The lowest runs with 2 workers on
the default day (5,503 and 6,234 lines/s, one per tree) fell into that loaded time (machine CPU
91–93 %). Re-measure on site with the site's own day (below).

Default `[ingest] workers = 2`: about 1.7× the in-process speed; a third worker adds 10–13 %
on 4 vCPU but competes with PostgreSQL, the API and the collector on the same cores. **Spec 14
(≥ 10,000 messages/s sustained on 4 vCPU) has not been shown with the real parsers**; it stays
an on-site acceptance item (`bench_throughput.py --repeat 3` and the load-generator mix on the
server). Spec volume averages 12–58 messages/s, so live operation is far below the limit; what
is slower than first documented is the catch-up of a backlog. The limit is CPU in the parse
workers (≈ 213 µs per line, ≈ 48 µs of it pickling the prepared chunk back to the service
process), not missing overlap: loading and parsing do overlap. A later optimisation could let
the workers return COPY-ready buffers instead of per-line objects.

Profile with the stub parsers (in-process, cProfile, 50,000 lines): `check_row` ≈ 53 % of the
CPU, parsing ≈ 16 %, raw-line decoding ≈ 8 %, loading (`COPY` etc.) ≈ 13 %. Parsing runs
in `spawn` worker processes (as on Windows) one batch ahead of loading; results are used in read
order, so offsets and exactly-once are unchanged, and a dead worker only means the chunk is
parsed in the service process. A broken pool is replaced after a wait of 5 s, doubling up to
5 minutes (reset after 10 minutes of good work); `ingest-status.json` → `parse_workers`
(`configured`, `active`, `restarts`, `last_error`) shows it. On POSIX the workers ignore only
`SIGINT` (not `SIGTERM`): CPython ends the survivors of a broken pool with `SIGTERM` while
holding the pool's lock, and an ignored `SIGTERM` blocked the whole service. A worker ends
itself when the service process is gone (killed, crashed, stopped by the service wrapper after
its timeout): it watches the parent's process sentinel, so no orphaned worker stays behind.

## Verify on site

- `open_shared` on the Windows server: the sealer can delete a spool file while ingest reads it
  (`test_files.py` runs there; the Windows-only test documents the plain-`open()` failure).
- Service wrapper (B7): send Ctrl+C or Ctrl+Break and allow ≥ 5 s before killing; grant the
  `NT SERVICE\ArciIngest` account read access to `spool\`, `vault\`, `state\seal\` and
  **modify** rights (create, write, delete files) on the `state\` folder: the status file is
  written atomically through `state\.ingest-status.json.tmp`, which is created and then renamed
  over `ingest-status.json` — write access to the file alone is not enough.
- Whether syslog senders with an IPv6 zone id (`fe80::1%12`) can occur (their lines get no
  `messages` row, see the table above).
- Throughput with the real parsers and the production database (TimescaleDB or partitions):
  `bench_throughput.py --repeat 3 --workers 0 2 3` and a live feed at 10,000 lines/s (spec 14
  acceptance; not met on the development VM); the cost of Wave 2 (the `counts` step and
  `ix_fg_web_rhost_time`) on the site's own mix, with its web filter lines, which
  `bench_throughput.py` does not have (the load-generator day above: −1.9 % / −3.8 %).
- The search counts after the first days: `arcilogger-db search-counts verify --hours 24` must
  report no difference (rows and counts are written in one transaction), and its line on names
  not stored in lower case should read 0 for `fg_dns.qname` and `fg_web.hostname` (`domain:`
  assumes lower case, ADR-0015).
- Readers of `ingest-status.json` (API) should open, read and close it quickly: on Windows an
  atomic replace fails while a reader holds the file; ingest logs that once and retries.
