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
  sayılır. Bu adımın kendi maliyeti tek süreçte bir satırın yükleme süresinin en çok yaklaşık
  %2,5'idir; hız ölçümleri çalışmadan çalışmaya oynamanın içinde kaldı (aşağıda "Measured
  throughput").
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
  `count_key` reads and makes each key once per group (about 1.9 µs a line instead of 5).
- A line whose typed row is not loaded — dropped by the row check (`rowcheck.<table>`) or refused
  by `COPY` and salvaged with its `messages` row alone (`copy.<table>`) — counts with `tbl = ''`:
  the search over the typed tables does not find it either. A line without a `messages` row
  (`rawlog.invalid`, `copy.messages`, a `rowcheck.messages` without generic view) is not
  counted: it is in neither search nor statistics, only in `parse_failures`.
- One `INSERT … ON CONFLICT … DO UPDATE SET n = search_counts.n + EXCLUDED.n` per 500 keys, keys
  in sorted order. A batch of 5,000 lines has tens to a few hundred keys; the load-generator day
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

**Wave 2: the `counts` step and the `fg_web` index (2026-10-26).** The same VM, but shared with
the test runs of the other Wave 2 packages (load average 2–9, the machine 30–100 % busy), so each
tree was measured in turn, round after round (`bench_throughput.py --lines 200000 --batch-rows
5000`, one run per round, and one load-generator day of 217,875 lines: 18 % web filter lines,
800 summary keys); min–max (median) lines/s:

| Trees compared | `bench_throughput.py` in-process / `--workers 2` | load-generator day in-process / `--workers 2` |
|---|---|---|
| Wave 1 (no index, no counts), 3 rounds | 4,445–4,997 (4,993) / 6,416–8,670 (8,162) | 3,953–4,370 (4,348) / 5,226–8,348 (7,662) |
| with the `fg_web` index, same rounds (6 / 3) | 4,400–5,091 (4,815) / 6,506–9,904 (8,204) | 4,008–4,535 (4,332) / 6,392–8,412 (7,153) |
| with the index, 4 later rounds | 4,153–5,298 (4,856) / 5,225–7,136 (6,316) | 4,000–4,892 (4,544) / 6,216–7,917 (6,654) |
| **with the index and the `counts` step**, the same 4 rounds | **4,486–5,614 (4,997) / 4,329–8,673 (7,278)** | **4,090–4,718 (4,372) / 5,777–6,734 (6,142)** |

The index costs nothing measurable (the benchmark has no web filter lines; the load-generator day
has 18 %). With the `counts` step the medians of `bench_throughput.py` are within the run-to-run
spread (+3 % in-process, +15 % with 2 workers), so the Wave 2 gate (at most 5 % slower) holds;
the load-generator day's medians are 4 % / 8 % lower, inside a spread of up to 27 % between two
runs of one round. The step's own cost, measured alone: 1.9 µs a line to make the keys (5 µs
before the lines were grouped by what `count_key` reads), and the upsert 3.5 ms per batch of
5,000 lines (2–7 keys) for the benchmark, 15 ms (37–247 keys) for the load-generator day
(measured with a commit of its own; in the load transaction there is none): 0.7–3 µs a line,
together at most about 2.5 % of the ≈ 200 µs an in-process line costs. With 2 workers the service
process does it in CPU time it has spare (it runs at 30–50 % of one core). Re-measure on site
with the rest (below).

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
  acceptance; not met on the development VM).
- The search counts after the first days: `arcilogger-db search-counts verify --hours 24` must
  report no difference (rows and counts are written in one transaction), and its line on names
  not stored in lower case should read 0 for `fg_dns.qname` and `fg_web.hostname` (`domain:`
  assumes lower case, ADR-0015).
- Readers of `ingest-status.json` (API) should open, read and close it quickly: on Windows an
  atomic replace fails while a reader holds the file; ingest logs that once and retries.
