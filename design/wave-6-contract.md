# Wave 6 contract: security rules R01–R19, the rule engine, the rule editor and webhooks

- **Status:** final, 2026-11-12. The owner started Wave 6 on 2026-11-12 ("şimdi sen wave 6'ya
  başla direkt"). The seven owner questions of the Wave 6 plan (2026-11-09) are not answered
  yet: every one ships the default written in §0.2 and is listed in "Owner questions" (§7). A
  package implements the default and does not decide the question.
- **Scope:** spec section 11 (rules R01–R19 beside the existing R15 and R20), the roadmap's
  rule engine and webhooks, the Security page additions, and the follow-ups noted for Wave 6
  (#217, #222, #227).
- **Base:** `wave-6` starts from `main` at `b90de60e` (release 0.4.0). Every `path:line` below
  was read there. Fixes released on `main` as 0.4.x are merged into `wave-6` by the manager.
- **Release:** Wave 6 ships as **0.5.0**.
- **Language:** the contract is in English; every user-facing text is Turkish first with English
  beside it. The only wording about the law is "5651 yükümlülüklerini desteklemek için
  tasarlanmıştır" / "designed to support 5651 obligations". The security rules are **not** a
  5651 obligation and no text says they are.
- **Example world only:** Örnek A.Ş., `172.20.x`, `00:00:5E:00:53:xx`, RFC 5737 addresses,
  `*.example` host names. No customer, person, device or site name appears anywhere.

---

## 0. Rules and decisions

### 0.1 Rules that bind every package

1. **Product rules** (`CLAUDE.md`, "Product rules") apply unchanged, and so do Wave 5's rules
   0.1 items 2, 3 and 6 (`design/wave-5-contract.md` §0.1).
2. **Rules only read.** The rule engine and the rules read the parsed tables, `devices`,
   `leases`, `persons` and `settings`. They never write a parsed table, a raw spool file, a
   vault file, a manifest or a token. They write only `alerts`, `alert_evidence`,
   `rule_state` and (through the existing store) `alert_events`.
3. **No SQL from users.** A rule is Python code in the registry (§2.1.2). An administrator
   changes only the values of its parameters (numbers, lists of strings, CIDR networks), which
   are validated by the rule's schema and passed to SQL as **bound parameters**. Nothing a user
   types is placed in an SQL string. Custom rules are a non-goal (Q6).
4. **Evidence is linked, not copied.** An alert names the rows that caused it (§2.1.5) by table,
   row id and raw reference (`raw_day`, `raw_line`, `raw_offset`, `source`). The raw line is
   opened through the existing `GET /api/v1/raw` with its permission and audit. An alert's
   `details`, its e-mail and its webhook never contain a raw log line.
5. **KVKK.**
   - E-mails and webhooks carry no person names and no raw log lines (unchanged rule).
   - A person-level rule (R09, R13, R14) stores the person as `details.person_id`, never a name.
     The API adds the name only for a user with `usage.person` and audits it (§2.6.3).
   - Titles name an IP address or a device MAC, never a person.
6. **No licence state blocks a 5651 function.** R15, R20, `ip_conflict` and `new_source` stay
   in **5651 Temel** and keep working with any licence. R01–R14, R16–R19 and the webhooks are the
   new module `security_rules` (Q1 default, §0.2 D1).
7. **Ingest is never slowed down.** Rules run in the worker, in their own transactions, with a
   statement timeout, on bounded slices (§2.1.3). The ingest throughput gates of earlier waves
   stay as they are; the rule engine adds its own performance guard (§3, T-A6).
8. **"Sahada doğrula".** Every log format the repository does not confirm with a format sample
   (`tests/fixtures/format/`) is a parameter with a default, the rule carries
   `format_verified: false` (shown as "sahada doğrula" in the editor), and a test fails loudly if
   the parser stops producing what the rule reads (§0.4).
9. **Thresholds are starting values, not measured facts.** Docs and the editor say so
   ("başlangıç değeri; ağınıza göre ayarlayın").

### 0.2 Decisions

| # | Decision |
|---|---|
| D1 | **Licence (Q1 default):** a new module `security_rules` ("Güvenlik kuralları" / "Security rules") in `core/src/arcilogger_core/license/modules.py`. The worker job `rules.run` has `module="security_rules"`; the rule and webhook write endpoints answer `module_disabled` (existing code) without it. The list of rules stays readable (the editor shows the lock). Changing the decision later is a change of the module key in one registry entry. |
| D2 | **Who sees alerts (Q2 default):** unchanged — `alerts.read` / `alerts.manage` (admin only, `api/src/arcilogger_api/security.py`). The rule editor needs `settings.manage`. |
| D3 | **Person names (Q3 default):** stored as `person_id`; shown on the Security page only to `usage.person` holders, audited as `alert.person_view`. Legal review recommended in the docs. |
| D4 | **Webhook targets (Q4 default):** configured **on the server only** (`[[webhooks]]` in `arcilogger.toml`, secrets with `arcilogger-core secret set`), like the backup folder (owner decision 2026-11-09, P5). The browser lists them, shows their deliveries and has "Test gönder". External cloud targets (Teams, Slack) are allowed only when the administrator writes them into the file; nothing is configured by default. |
| D5 | **Work hours (Q5 default):** R04 and R14 read `usage.work_hours` and `usage.holidays` even without the `usage_analytics` licence: one shared setting. |
| D6 | **Custom rules (Q6 default):** not in Wave 6. Only R01–R19's parameters are editable. |
| D7 | **Sample lines (Q7 default):** without field samples the rules that read unconfirmed formats ship with `format_verified: false` (§0.4). |
| D8 | **Windows run on receive time.** A rule counts rows by `time_received` (the collector's clock), not by device time: a device clock that is minutes off (#222) does not hide or split a burst. Device time is shown in the evidence. |
| D9 | **Two kinds of rule.** `event` rules (R01–R14, R16, R18, R19) open an alert per key and never resolve it automatically; a person closes it. `condition` rules (R17) resolve their alert themselves when the condition has been absent for `clear_seconds`. |
| D10 | **One delivery table.** Webhook deliveries are rows of the existing `alert_deliveries` with `kind = 'webhook'` and `recipient = <webhook name>`; the e-mail code ignores them, the webhook job ignores the others. |
| D11 | **Rule alert e-mail.** A rule alert is e-mailed by the existing `alerts.notify` when its rule's action `email` is on **and** its severity reaches `alerts.email_min_severity` (unchanged rate limit and digest). |
| D12 | **No backfill flood.** A rule that is switched on (or runs for the first time) starts at `now − rules.lag_seconds`; it never raises alerts for older data. R11 seeds its known MACs from `devices` before its first run (§2.2.4). |

### 0.3 Non-goals of Wave 6

- Rules written by administrators, any rule language typed in the browser (Q6).
- A separate "Güvenlik" role (Q2).
- Machine learning, anomaly scores, threat-intelligence downloads (the threat list is the
  FortiGate's own, R06).
- Configuring webhook targets from the browser (Q4).
- Changing any parser's output for existing tables. A rule that needs a field the parser keeps
  only in `extra` reads it from `extra` (§2.2).

### 0.4 "Sahada doğrula" list (each has a test that fails loudly)

| Id | Assumption | Parameter | Test |
|---|---|---|---|
| V1 | FortiGate admin login logids: failed `0100032002`, successful `0100032001` (match also on `logdesc`) | `R03.fortigate_fail_logids`, `R04.fortigate_ok_logids` | `test_r03_r04_formats` reads `tests/fixtures/synthetic/` lines through the real parser and asserts the rows the rules match |
| V2 | FortiGate configuration-change logids `0100044546`, `0100044547` | `R16.fortigate_logids` | `test_r16_formats` |
| V3 | FortiGate performance statistics: `fg_event` rows with logid `0100040704`, `cpu` and `mem` in `extra` | `R17.perf_logids`, `R17.cpu_field`, `R17.mem_field` | `test_r17_formats` |
| V4 | Conserve mode is announced by an `fg_event` row whose `logdesc` contains `conserve` | `R17.conserve_phrases` | `test_r17_formats` |
| V5 | FortiGate DHCP pool exhaustion: `fg_event` `logdesc` contains `ran out of` | `R18.fortigate_phrases` | `test_r18_formats` (MikroTik `dhcp_pool_empty` is a confirmed parser family) |
| V6 | FortiGate DNS log response code in `extra["rcode"]`, NXDOMAIN value `NXDOMAIN` (also numeric `3`) | `R19.rcode_field`, `R19.nxdomain_values` | `test_r19_formats` |
| V7 | Botnet detections arrive as `fg_ips` rows with `eventtype` `botnet` | `R06.ips_eventtypes` | `test_r06_formats` |
| V8 | Teams (Workflows, Adaptive Card) and Slack (incoming webhook `{"text"}`) accept the payloads of §2.4.3 | `[[webhooks]] format` | `test_webhook_payload_shapes` pins the JSON; the doc marks both "sahada doğrula" |

Each test above builds its rows from fixture lines through `arcilogger_ingest.parsers`, then
runs the rule on them. When a parser change stops producing the field, the test fails with the
id (V1…V8) in its message.

---

## 1. Facts this contract rests on

- **Alerts** (`db/src/arcilogger_db/models.py:1330-1366`): `alerts(id, time, rule, severity,
  state, title, details JSONB, dedup_key, created_at, updated_at, resolved_at)`; partial unique
  index `uq_alerts_open_dedup_key` on open alerts. Severities `ALERT_SEVERITIES` = info, low,
  medium, high, critical. The EN title lives in `details.title_en`
  (`worker/src/arcilogger_worker/alerts/license.py:115`).
- **Store** (`worker/src/arcilogger_worker/alerts/store.py`): `open_alert(...)` opens or
  updates the active alert of a key (`:133-170`); a person's resolution is never re-opened by a
  check; every change of state is an `alert_events` row.
- **Deliveries** (`models.py:1368-1405`): `alert_deliveries(alert_id, kind, recipient, status,
  attempts, last_error, next_attempt_at, message_id, created_at, sent_at)`, unique
  `(alert_id, kind, recipient)`.
- **Events** (`models.py:1727-1775`): `alert_events.kind` is `state` or `note`, checked by the
  `shape` constraint.
- **Worker jobs** (`worker/src/arcilogger_worker/service.py:410-455`): `Job(name, fn, every=…,
  module=…)`; the alert jobs are `alerts.import`, `alerts.r15`, `alerts.system`,
  `alerts.notify`, in that order.
- **Parsed tables** (`models.py:200-620`), every one with `id, time, time_received, source,
  sender, extra, raw_day, raw_line, raw_offset`:
  - `fg_traffic` (action, policyname, srcip, srcport, srcintf, srcmac, dstip, dstport, dstintf,
    service, app, appcat, sentbyte, rcvdbyte, sentdelta, rcvddelta, is_interim, …);
  - `fg_web` and `fg_dns` (srcip, hostname/qname, cat, catdesc, action, eventtype, …);
  - `fg_app` (srcip, app, appid, appcat, apprisk, action, …);
  - `fg_ips` (srcip, dstip, attack, attackid, severity, action, eventtype, …);
  - `fg_virus` (srcip, dstip, virus, virusid, filename, url, hostname, action, eventtype, …);
  - `fg_event` (logid, subtype, level, logdesc, action, status, user_name, ui, srcip, cfgpath,
    cfgobj, cfgattr, msg);
  - `fg_dhcp`, `mt_dhcp`, `mt_arp` (ip, mac, …);
  - `mt_fw` (prefix, chain, in_if, out_if, src, sport, dst, dport, …);
  - `mt_account` (action `login` / `logout` / `login_failure`, user_name, ip, via);
  - `mt_config` (action, user_name, target, via);
  - `mt_event` (event, …; `dhcp_pool_empty` is a parser family,
    `ingest/src/arcilogger_ingest/parsers/mikrotik.py:44-46`).
- **Devices** (`models.py:810-845`): `devices(mac, first_seen, last_seen, last_ip, last_vlan,
  randomized, infrastructure, person_id, …)`, written by
  `worker/src/arcilogger_worker/correlation/devices.py:110`.
- **Raw lines:** `GET /api/v1/raw?source=&day=&line=&offset=`
  (`api/src/arcilogger_api/routers/raw.py:193`), permission `search.read` or `lookup.run`,
  audited `raw.view`.
- **Licence modules** (`core/src/arcilogger_core/license/modules.py:30-80`): `core_5651`
  (always), `usage_analytics`, `usage_reports`, `bulk_import`.
- **Settings** (`db/src/arcilogger_db/settings.py`): database settings with defaults;
  `alerts.email_min_severity` "high", `usage.work_hours`, `usage.holidays`.
- **SMTP** (`core/src/arcilogger_core/smtp.py`), **secret store**
  (`core/src/arcilogger_core/secretstore.py`), **administration agent probes**
  (`core/src/arcilogger_core/admin/probe.py`: `sntp`, `credit`, `smtp`, `replica`, `snmp`,
  `backup_mirror`).
- **Strict outbound firewall** (`deploy/windows/scripts/Set-ArciFirewall.ps1:460-580`): one
  `ARCILOGGER Out …` rule per outbound purpose.
- **Web:** Security page `web/src/pages/security/` (`security-page.tsx`, `alert-sheet.tsx`,
  `security-summary.tsx`, `alerts-model.ts`); settings pages `web/src/pages/settings/`.

---

## 2. Design

### 2.1 The rule engine (W6-A)

#### 2.1.1 Package

`worker/src/arcilogger_worker/rules/`:

| Module | Content |
|---|---|
| `model.py` | `RuleDef`, `Param`, `RuleSettings`, `Hit`, `EvidenceRef` (§2.1.2) |
| `registry.py` | `RULES: tuple[RuleDef, ...]` in id order; `rule(id)`; the import of every rule module |
| `engine.py` | `run_rules(sessions, layout, now)`: slices, transactions, dedup, evidence, `rule_state` |
| `settings.py` | reading and validating `rules.<ID>` (§2.1.4) |
| `dryrun.py` | `dry_run(session, rule_id, candidate, now)` (§2.1.6) |
| `texts.py` | names, title templates and advice of every rule (TR/EN) |
| `network.py` | R01, R02, R08, R10, R19 (W6-B) |
| `access.py` | R03, R04, R11, R12, R16 (W6-C) |
| `threats.py` | R05, R06, R07, R17, R18 (W6-D) |
| `people.py` | R09, R13, R14 (W6-E) |

Until a rule package lands, its module holds no entries. W6-A's first commit adds every module
with an empty list and `texts.py` with the 18 entries of §2.2 (names and titles fixed here), so
the rule packages only add their `RuleDef`s.

#### 2.1.2 A rule

```python
@dataclass(frozen=True)
class Param:
    key: str  # e.g. "threshold_ports"
    kind: Literal["int", "bytes", "seconds", "percent", "strings", "networks", "ints", "bool"]
    default: object
    min: int | None = None  # int / bytes / seconds / percent; list length for lists
    max: int | None = None
    label_tr: str = ""
    label_en: str = ""


@dataclass(frozen=True)
class RuleDef:
    id: str  # "R01"
    kind: Literal["event", "condition"]
    severity: str  # default severity (ALERT_SEVERITIES)
    window_seconds: int  # default window
    cooldown_seconds: int  # default cooldown
    params: tuple[Param, ...]  # rule-specific parameters
    inputs: tuple[str, ...]  # tables read, e.g. ("fg_traffic",)
    person_level: bool = False  # R09, R13, R14
    format_verified: bool = True  # False: "sahada doğrula" (§0.4)
    needs_setup: Callable[[RuleSettings], bool] | None = None  # True: idle until configured
    evaluate: Callable[[Session, RuleContext], Iterable[Hit]]
    clear_seconds: int | None = None  # condition rules only


@dataclass(frozen=True)
class Hit:
    key: str  # group value, e.g. "172.20.41.17" (dedup key R01:<key>)
    time: datetime  # receive time of the newest row of the hit
    count: int  # rows behind the hit in its window
    values: Mapping[str, object]  # the numbers the title and details show
    evidence: Sequence[EvidenceRef]  # at most rules.evidence_max, oldest first
    person_id: int | None = None  # person-level rules only
```

`RuleContext` gives the evaluating function: the validated settings, the slice
`(start, end]` (receive time), the window start `end − window_seconds`, the device-time
bounds for partition pruning (`start − rules.max_device_skew_seconds`,
`end + rules.max_device_skew_seconds`), `rules.internal_networks`, and the site time zone.

**Every query of a rule:**

- bounds `time` (device time) to the pruning bounds **and** `time_received` to the window;
- selects only the group keys that have at least one row in the slice;
- passes every parameter as a bound parameter (lists as arrays, networks as `cidr[]` with
  `<<=`);
- returns evidence rows with `id, time, time_received, source, raw_day, raw_line,
  raw_offset`.

#### 2.1.3 The job `rules.run`

- `Job("rules.run", job_rules_run, every=timedelta(seconds=60), module="security_rules")`,
  after `alerts.system` and before `alerts.notify` (the e-mail sends what the rules raised).
- **Horizon** `H = now − rules.lag_seconds` (default 120).
- **Per rule**, enabled and not `needs_setup`:
  1. Watermark `W = rule_state.watermark`. If missing or the rule was off since the last run:
     `W = H` (D12) and nothing is evaluated this run.
  2. If `H − W > rules.max_catch_up_hours` (24): `W = H − 24 h` and
     `rule_state.skipped_until` records the skipped time (shown in the editor).
  3. Slices of at most `rules.max_slice_seconds` (600) from `W` to `H`; for each slice, one
     transaction with `SET LOCAL statement_timeout = rules.statement_timeout_seconds` (20 s):
     evaluate, open or update alerts (§2.1.5), store evidence, move the watermark to the slice
     end, commit.
  4. An exception or a timeout rolls the slice back, records `rule_state.last_error` and
     `failures + 1`, and the job goes on with the next rule. After
     `rules.failures_alert` (3) consecutive failures: system alert `R20-rules` (high, key
     `R20-rules:<ID>`, "Güvenlik kuralı çalışamıyor: R01" / "Security rule cannot run: R01"),
     resolved automatically after the next successful slice.
- **No data.** After each run `rule_state.last_input_at` is the newest `time_received` among
  the rule's `inputs` (one `max(time_received)` per table with the pruning bound, cached per
  run). The API reports `data_status` (§2.6.1): `ok`, `no_data` (nothing within
  `rules.no_data_hours`, 24) or `never`.
- **Condition rules** (R17): each run also resolves the active alert of every key that had no
  hit for `clear_seconds`, with the reason code `condition_cleared`.

#### 2.1.4 Settings

Database settings (defaults in `db/src/arcilogger_db/settings.py`, area "Rules", W6-A):

| Key | Default | Meaning |
|---|---|---|
| `rules.lag_seconds` | 120 | data younger than this is left for the next run |
| `rules.max_slice_seconds` | 600 | largest slice in one transaction |
| `rules.max_catch_up_hours` | 24 | older backlog is skipped and recorded |
| `rules.statement_timeout_seconds` | 20 | per slice |
| `rules.failures_alert` | 3 | consecutive failures before `R20-rules` |
| `rules.evidence_max` | 20 | evidence rows kept per alert |
| `rules.no_data_hours` | 24 | `data_status` `no_data` after this |
| `rules.max_device_skew_seconds` | 3600 | device-time margin for partition pruning |
| `rules.internal_networks` | `["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]` | "iç adres" for every rule |
| `rules.dry_run_timeout_seconds` | 30 | §2.1.6 |
| `rules.<ID>` | `{}` | the overrides of one rule (below) |

`rules.<ID>` holds only what differs from the registry defaults:

```json
{"enabled": true, "severity": "high", "window_seconds": 300, "cooldown_seconds": 3600,
 "actions": {"email": true, "webhook": true}, "params": {"threshold": 5}}
```

- `enabled` default `true` for every rule except those whose `needs_setup` is true with the
  defaults (they are idle until configured; the editor says "Ayar gerekli" / "Needs setup").
- `actions.email` default `true` for severity high and critical, `false` below;
  `actions.webhook` default `true`.
- Validation (`rules/settings.py`): unknown keys, a severity outside `ALERT_SEVERITIES`, a
  window below 60 s or above 7 days, a cooldown above 7 days, a parameter outside its
  `min`/`max`, a list longer than 200 entries, a string longer than 200 characters, an invalid
  CIDR: error `rule_param_invalid` with `field`. A stored value that no longer validates (a
  downgrade, a hand edit) is ignored field by field with a warning in the worker log, and the
  default applies.
- The generic settings page does not list `rules.*` keys (`settings_meta.py` marks the area as
  edited elsewhere); `GET /api/v1/settings` still returns them for backups.

#### 2.1.5 Alerts and evidence of a rule

- `rule` = the rule id; `dedup_key` = `<ID>:<hit.key>`; `time` = `hit.time`; severity = the
  rule's setting.
- `title` = TR template of §2.2 with `hit.values`; `details`:

```json
{"title_en": "…", "rule_kind": "event", "key": "172.20.41.17", "count": 47,
 "values": {"ports": 47, "window_seconds": 60}, "first_time": "…", "last_time": "…",
 "actions": {"email": true, "webhook": true}, "person_id": null,
 "format_verified": true, "evidence_total": 47}
```

- **An active alert of the same key** is updated: `count` and `evidence_total` add up,
  `last_time` moves, `values` show the latest window, evidence is appended until
  `rules.evidence_max`. The title is not rewritten more often than the store already allows.
- **Cooldown:** no new alert for a key within `cooldown_seconds` after the `time` of the newest
  closed alert of that key. Hits inside the cooldown are counted on that closed alert's
  `details.after_close` (count and last time) and not shown as a new alert.
- **Evidence** table `alert_evidence` (W6-A migration, §2.5): up to `rules.evidence_max` rows per
  alert, oldest first, never updated, deleted with the alert.

#### 2.1.6 Dry run ("Son 24 saatte dene")

`dry_run(session, rule_id, candidate_settings, now)` evaluates the rule with the candidate
settings over `(now − 24 h − lag, now − lag]` in slices of `rules.max_slice_seconds`, in one
**read-only** transaction (`SET TRANSACTION READ ONLY`) with
`statement_timeout = rules.dry_run_timeout_seconds`. It returns the number of alerts that
**would** have opened (dedup and cooldown applied as if no alert existed), the first 20 keys
with their counts and first and last times, `truncated`, and `duration_ms`. It writes nothing
and opens no alert. One dry run at a time per API process (`rule_dry_run_busy`).

#### 2.1.7 Performance (T-A6)

With every rule enabled and configured (the loadgen site, §2.7), the job finishes one 60-s
slice of **10,000 messages per second** (600,000 rows, the loadgen's table mix) in **at most
20 s** on the Linux CI runner, plain PostgreSQL layout; and ingest's existing throughput gate
passes while `rules.run` runs. W6-A adds the indexes the rules need in its migration, each
measured (`EXPLAIN` in the pull request), none on a column the rules do not filter.

### 2.2 The rules

Defaults are spec section 11's. "Key" is the dedup group. Every rule's advice ("Ne yapılmalı"
/ "What to do") is in `rules/texts.py` and in the docs (§2.8). Titles are templates with the
values in braces.

#### 2.2.1 W6-B — network (`rules/network.py`)

| Id | Name (TR / EN) | Input and match | Key | Window, threshold | Sev. | Notes |
|---|---|---|---|---|---|---|
| R01 | İçeriden port taraması / Internal port scan | `fg_traffic` with `action` in `deny_actions` (`["deny"]`), `srcip` internal; optionally `mt_fw` rows whose `prefix` is in `mikrotik_prefixes` (`[]`) | srcip | 60 s; distinct `dstport` ≥ `threshold_ports` (30) **or** distinct `dstip` ≥ `threshold_hosts` (50) | high | title "İç adres {key} {ports} farklı porta / {hosts} adrese engellendi" |
| R02 | Dışarıdan tarama / External scan | `fg_traffic` `action` in `deny_actions`, `srcip` not internal, `policyname` in `policy_names` (`[]` = any) | srcip | 300 s; ≥ `threshold` (100) | medium | cooldown 1 h |
| R08 | DNS atlatma / DNS bypass | `fg_traffic` internal `srcip`, `dstport` in `ports` (`[53, 853]`), `dstip` not in `allowed_dns_servers` | srcip | 3600 s; ≥ `threshold` (20) | medium | `needs_setup` while `allowed_dns_servers` is empty |
| R10 | Proxy / VPN / Tor | `fg_web` or `fg_dns` with `catdesc` in `categories` (`["Proxy Avoidance"]`), or `fg_app` with `app` in `apps` (`["Tor"]`) or `appcat` in `appcats` (`["Proxy"]`) | srcip | 3600 s; ≥ `threshold` (1) | medium | cooldown 1 h; the category and app names are FortiGuard's (sahada doğrula for the site's FortiOS) |
| R19 | Şüpheli DNS (DGA) / Suspicious DNS (DGA) | `fg_dns` whose `extra[rcode_field]` is in `nxdomain_values` (V6) | srcip | 3600 s; > `threshold` (200) | medium | `format_verified: false` |

#### 2.2.2 W6-C — access and devices (`rules/access.py`)

| Id | Name | Input and match | Key | Window, threshold | Sev. | Notes |
|---|---|---|---|---|---|---|
| R03 | Kaba kuvvet giriş denemesi / Brute-force login | `fg_event` with `logid` in `fortigate_fail_logids` or `logdesc` in `fortigate_fail_descs` (`["Admin login failed"]`) (V1); `mt_account` with `action = 'login_failure'` | `user:<name>` and `ip:<srcip>` (two keys, each counted) | 300 s; ≥ `threshold` (5) | high | `format_verified: false` (FortiGate part) |
| R04 | Mesai dışı / beklenmeyen yerden yönetici girişi / Admin login off hours or from an unexpected address | successful logins: `fg_event` `logid` in `fortigate_ok_logids` or `logdesc` in `fortigate_ok_descs` (`["Admin login successful"]`) (V1); `mt_account` `action = 'login'` | `user:<name>` | each login; off hours by `usage.work_hours`/`usage.holidays` (D5) **or** `srcip` not in `trusted_admin_networks` (`[]` = this part off) | medium | `off_hours` param (bool, default true) switches the first part |
| R11 | Yeni cihaz / New device | a `devices` row whose `first_seen` is in the slice, `infrastructure` false, not `randomized` when `ignore_random` (true), `last_vlan` not in `ignore_vlans` (`[]`) | mac | each device | low | seeding: before the first evaluation the watermark is set (D12); devices created before it never alert |
| R12 | MAC değişimi / MAC change | `mt_arp`, `mt_dhcp` (`assigned`), `fg_dhcp`: the same `ip` seen with ≥ `threshold` (3) MAC changes; MACs in `devices.infrastructure_macs` and infrastructure devices are ignored | ip | 3600 s | medium | the existing `ip_conflict` alert (5651 Temel) stays as it is |
| R16 | Cihaz yapılandırma değişikliği / Device configuration change | `fg_event` `logid` in `fortigate_logids` (V2); every `mt_config` row | `<source>:<user>` | each change; cooldown 600 s (changes inside it are counted) | medium | `format_verified: false` (FortiGate part) |

#### 2.2.3 W6-D — threats and health (`rules/threats.py`)

| Id | Name | Input and match | Key | Window, threshold | Sev. | Notes |
|---|---|---|---|---|---|---|
| R05 | IPS saldırısı / IPS attack | `fg_ips`: `severity` in `high_severities` (`["high", "critical"]`) each; `severity` in `medium_severities` (`["medium"]`) ≥ `threshold_medium` (10) per hour | `<srcip>:<attackid>` | each / 3600 s | high | cooldown 1 h |
| R06 | Botnet / tehdit IP / Botnet or threat IP | `fg_ips` with `eventtype` in `ips_eventtypes` (`["botnet"]`, V7); `fg_traffic` with `policyname` in `threat_policy_names` (`[]`) | srcip | each | critical | `needs_setup` is false (the IPS part works alone); `format_verified: false` |
| R07 | Zararlı yazılım / Malware | every `fg_virus` row except `action` in `ignore_actions` (`[]`) | `<srcip>:<virusid>` | each | high | title: the virus name and the source address only (below) |
| R17 | Güvenlik duvarı sağlığı / Firewall health | `fg_event` perf rows (V3): CPU > `cpu_percent` (80) or memory > `mem_percent` (85) in every sample for `sustain_seconds` (600); or a conserve-mode row (V4) | source | condition; `clear_seconds` 600 | high | `kind: condition`; `format_verified: false` |
| R18 | DHCP havuzu doldu / DHCP pool exhausted | `mt_event` `event = 'dhcp_pool_empty'`; `fg_event` `logdesc` containing one of `fortigate_phrases` (V5) | `<source>:<server or interface>` | each; cooldown 3600 s | high | |

R07's title is "Zararlı yazılım: {virus} ({srcip})" / "Malware: {virus} ({srcip})". The file
name and URL stay in the evidence (raw line), not in the title or details.

#### 2.2.4 W6-E — people and usage (`rules/people.py`; `person_level: true`)

| Id | Name | Input and match | Key | Window, threshold | Sev. | Notes |
|---|---|---|---|---|---|---|
| R09 | Engelli kategori ısrarı / Repeated blocked categories | `fg_web`, `fg_dns` with `action` in `blocked_actions` (`["blocked"]`) | the person of `srcip` at the row's device time (lease attribution, `arcilogger_worker.lookup`'s attribution helper); `ip:<srcip>` when nobody is attributed | 3600 s; ≥ `threshold` (20) | low | |
| R13 | Büyük veri gönderimi / Large upload | `fg_traffic` bytes **sent**, counted as the usage rollups count them (`arcilogger_worker.usage`: final rows' `sentbyte`, interim rows' `sentdelta`, no double count) | person, else `ip:<srcip>` | > `hour_bytes` (1 GiB) per 3600 s **or** > `day_bytes` (5 GiB) per site day | medium | |
| R14 | Gece etkinliği / Night activity | `fg_traffic` from `srcip` in `user_networks` (`[]`) between `night_start` ("00:00") and `night_end` ("06:00") site time, ≥ `min_bytes` (10 MiB) | person, else `ip:<srcip>` | per night | low | `needs_setup` while `user_networks` is empty |

`details.person_id` is set; the title names the IP (`{srcip}`) only.

### 2.3 Rule editor (W6-A API, W6-F web)

`Ayarlar → Güvenlik kuralları` (route `/settings/rules`), permission `settings.manage` to
change, `alerts.read` to view.

- A table: code, name, severity, on/off, data status ("Veri geliyor" / "Son 24 saatte veri yok"
  / "Hiç veri gelmedi"), "Sahada doğrula" badge when `format_verified` is false, "Ayar gerekli"
  when `needs_setup`, the lock when `security_rules` is not licensed.
- A sheet per rule: severity, window, threshold(s), cooldown, the rule's parameters (typed
  inputs; lists one entry per line; networks validated as CIDR), actions (e-mail, webhook), the
  advice text, the "başlangıç değeri" note, "Son 24 saatte dene" and Save.
- "Son 24 saatte dene" shows: "Son 24 saatte bu ayarlarla {n} uyarı açılırdı" / "With these
  settings {n} alerts would have opened in the last 24 hours", and the first keys.
- Save writes `rules.<ID>` and the audit entry `rule.update` with `{"rule", "before",
  "after"}` (the full effective settings, no person data).

### 2.4 Webhooks (W6-H)

#### 2.4.1 Configuration (server only, D4)

```toml
[[webhooks]]
name = "siem"                     # [a-z0-9-]{1,32}, unique
url = "https://siem.ornek.example/arcilogger"   # https only
format = "generic"                # generic | teams | slack
min_severity = "high"             # ALERT_SEVERITIES
rules = []                        # [] = every rule, R15/R20 included; else the listed ids
enabled = true
timeout_seconds = 10
ca_file = ""                      # optional PEM; the system store otherwise
```

- Secret `webhook.<name>.secret` (`arcilogger-core secret set webhook.siem.secret`), 32–256
  characters, required for `generic` (HMAC), unused by `teams`/`slack` (their URL is the
  secret: the URL of a `teams`/`slack` webhook is read from `webhook.<name>.url` instead of the
  file and `url` stays empty in the file).
- Validation in `core/src/arcilogger_core/config.py` (`WebhookConfig`): https only, no user
  info in the URL, host not an IP literal in `127.0.0.0/8`/`::1`, unique names, at most 10.
- **TLS verification is always on**; there is no switch to turn it off.
- `Set-ArciFirewall.ps1` (strict outbound mode) adds one rule `ARCILOGGER Out Webhook <name>`
  per enabled webhook (TCP 443 or the URL's port, the host's resolved addresses), the same way
  as `ARCILOGGER Out SMTP`.

#### 2.4.2 Delivery

- Job `alerts.webhooks` (every 60 s, `module="security_rules"`, after `alerts.notify`): for
  every enabled webhook, queue `alert_deliveries(kind='webhook', recipient=<name>)` for each
  alert that is new since the webhook's start (opened after the first run with this webhook;
  no backfill), matches `rules` and `min_severity`, and whose rule's `actions.webhook` is not
  false (alerts of R15, R20 and the other Temel rules have no actions and count as true).
- Send: `POST` with `Content-Type: application/json; charset=utf-8`, `User-Agent:
  ARCILOGGER/<version>`, through `[network] https_proxy` when set; 2xx = `sent`.
- Retry: 4xx except 408/429 is permanent; everything else is retried after 1, 5, 15, 60
  minutes and then hourly until 24 h after the first attempt; then `failed`.
- A failed delivery (permanent or after 24 h) opens `R20-webhook` (high, key
  `R20-webhook:<name>`, "Webhook teslim edilemiyor: {name}" / "Webhook cannot deliver:
  {name}"), resolved by the next `sent` delivery of that webhook. `R20-webhook` itself is
  never sent to the failing webhook.
- `last_error`: status code and at most 300 characters of the reply, without the URL's query,
  the secret or any header value.

#### 2.4.3 Payloads (no person names, no raw lines)

`generic`:

```json
{"product": "ARCILOGGER", "version": "0.5.0", "site": "<alerts.web_base_url host>",
 "alert": {"id": 412, "rule": "R01", "severity": "high", "state": "open",
           "title_tr": "…", "title_en": "…", "time": "2026-11-12T09:41:07+03:00",
           "count": 47, "url": "https://…/security?alert=412"}}
```

Headers `X-Arcilogger-Timestamp: <unix seconds>` and `X-Arcilogger-Signature:
sha256=<hex HMAC-SHA256 of "<timestamp>.<body>">` with the secret.

`slack`: `{"text": "[ARCILOGGER] <SEVERITY TR> — <title_tr> (<url>)"}`.

`teams`: `{"type": "message", "attachments": [{"contentType":
"application/vnd.microsoft.card.adaptive", "content": {"type": "AdaptiveCard", "version":
"1.4", "body": [{"type": "TextBlock", "text": "<severity> — <title_tr>", "wrap": true},
{"type": "TextBlock", "text": "<title_en>", "wrap": true, "isSubtle": true}], "actions":
[{"type": "Action.OpenUrl", "title": "Aç / Open", "url": "<url>"}]}}]}`.

Both `slack` and `teams` are "sahada doğrula" (V8).

#### 2.4.4 Test and API

- Administration agent probe kind `webhook` (`core/src/arcilogger_core/admin/probe.py`): sends a
  test payload (`"rule": "TEST"`, "ARCILOGGER deneme bildirimi" / "ARCILOGGER test
  notification") to one configured webhook; result `ok` with the status code, or
  `webhook_test_failed` with the error text of §2.4.2.
- `GET /api/v1/webhooks` (`alerts.read`): name, format, host (never the path or query),
  min_severity, rules, enabled, last delivery (time, status), counts of the last 7 days.
- `GET /api/v1/webhooks/{name}/deliveries?limit=50` (`alerts.read`).
- `POST /api/v1/webhooks/{name}/test` (`settings.manage`, module `security_rules`): queues the
  probe through `admin_requests` as the SMTP trial does; the web polls the request.

### 2.5 Schema (W6-A, one migration `v0024_rules`)

- `rule_state(rule text PK, watermark timestamptz, last_run_at timestamptz, last_input_at
  timestamptz, last_error text, failures int NOT NULL DEFAULT 0, skipped_until timestamptz,
  alerts_opened bigint NOT NULL DEFAULT 0, updated_at timestamptz)`.
- `alert_evidence(alert_id bigint FK alerts ON DELETE CASCADE, n smallint, tbl text, row_id
  bigint, time timestamptz, time_received timestamptz, source text, raw_day date, raw_line
  int, raw_offset bigint, PRIMARY KEY (alert_id, n))`; `tbl` checked against the parsed tables
  and `devices`.
- `alerts.assignee_id bigint NULL FK users ON DELETE SET NULL`, index
  `ix_alerts_assignee_state (assignee_id, state)`.
- `alert_events.kind` adds `assign` (`from_state`/`to_state` NULL, `text` = the new assignee's
  user name or NULL for "nobody"); the `shape` constraint is extended.
- `ALERT_DELIVERY_KINDS` adds `webhook`.
- The indexes the rules need (§2.1.7), each justified in the pull request.
- Downgrade drops exactly these.

### 2.6 API (W6-A unless noted)

#### 2.6.1 Rules

- `GET /api/v1/rules` (`alerts.read`):

```json
{"module_enabled": true,
 "rules": [{"id": "R01", "name_tr": "…", "name_en": "…", "kind": "event",
            "inputs": ["fg_traffic"], "person_level": false, "format_verified": true,
            "needs_setup": false, "data_status": "ok", "last_input_at": "…",
            "last_run_at": "…", "last_error": null, "skipped_until": null,
            "defaults": {…}, "settings": {…}, "params": [{"key": "threshold_ports",
            "kind": "int", "min": 1, "max": 65535, "label_tr": "…", "label_en": "…"}],
            "advice_tr": "…", "advice_en": "…"}]}
```

- `PUT /api/v1/rules/{id}` (`settings.manage`, module): body = `rules.<ID>` shape; answers the
  rule as above. Errors `rule_not_found` (404), `rule_param_invalid` (422, `field`),
  `module_disabled` (existing).
- `POST /api/v1/rules/{id}/dry-run` (`settings.manage`, module): body = candidate settings;
  answer `{"would_open": 3, "keys": [{"key", "count", "first_time", "last_time"}],
  "truncated": false, "duration_ms": 812}`. Errors `rule_param_invalid`,
  `rule_dry_run_busy` (409), `rule_dry_run_timeout` (504).

#### 2.6.2 Alerts (additions)

- `GET /api/v1/alerts?assignee=me|none|<user id>` filter; each alert gains `assignee`
  (`{"id", "username"}` or null) and `evidence_total`.
- `POST /api/v1/alerts/{id}/assign` (`alerts.manage`) `{"user_id": 7 | null}`: only users with
  `alerts.read`; writes an `assign` event and the audit `alert.assign`. Error
  `alert_assignee_invalid` (422).
- `GET /api/v1/alerts/{id}/evidence` (`alerts.read`): the evidence rows with
  `raw: {"source", "day", "line", "offset"}` for `/raw`; no line content.
- `GET /api/v1/alerts/summary` gains `by_day` (the last 30 site days, counts per severity) and
  `top_rules`, `top_keys` (the 10 largest of the last 7 days).

#### 2.6.3 Person names (D3)

`GET /api/v1/alerts/{id}` adds `person: {"id", "name"}` only for a user with `usage.person`
and only for an alert of a person-level rule; each such answer writes `alert.person_view`
(`{"alert_id", "person_id"}`) before it is returned.

#### 2.6.4 Error texts

| Code | TR | EN |
|---|---|---|
| `rule_not_found` | Böyle bir güvenlik kuralı yok. | No such security rule. |
| `rule_param_invalid` | Kural ayarı geçersiz: {field}. | Invalid rule setting: {field}. |
| `rule_dry_run_busy` | Başka bir deneme sürüyor; bitince tekrar deneyin. | Another trial run is in progress; try again when it has finished. |
| `rule_dry_run_timeout` | Deneme {n} sn içinde bitmedi; pencereyi ya da listeleri küçültün. | The trial run did not finish within {n} s; shorten the window or the lists. |
| `alert_assignee_invalid` | Bu kullanıcıya uyarı atanamaz. | Alerts cannot be assigned to this user. |
| `webhook_not_found` | Böyle bir webhook yok. | No such webhook. |
| `webhook_test_failed` | Deneme bildirimi gönderilemedi: {error} | The test notification could not be sent: {error} |

### 2.7 Load generator scenarios (W6-I)

`tools/loadgen/arcilogger_loadgen.py --scenario <name>[,<name>…]` adds fictional attack traffic
to the normal site: `port-scan` (R01), `external-scan` (R02), `brute-force` (R03),
`off-hours-admin` (R04), `ips` (R05), `botnet` (R06), `malware` (R07), `dns-bypass` (R08),
`blocked-category` (R09), `tor` (R10), `new-device` (R11), `mac-flap` (R12), `big-upload`
(R13), `night` (R14), `config-change` (R16), `fw-health` (R17), `pool-empty` (R18), `dga` (R19),
`all`. Every scenario line is synthetic, from the example world, in the formats of
`tests/fixtures/` (synthetic where no format sample exists, README says so). The perf guard
(§2.1.7) and the demo use `all`.

### 2.8 Docs (W6-I)

- `docs/UYARILAR.md` / `docs/ALERTS.md`: a section per rule (what it catches, default
  thresholds, data it needs, "Ne yapılmalı" / "What to do", "sahada doğrula" where it applies),
  the rule editor, the dry run, assignment, evidence links.
- `docs/DEVICE-SETUP.md`: FortiGate settings that send IPS, antivirus, application control,
  admin login, configuration change and performance logs to syslog; MikroTik `account`,
  `system` topics. Every command is marked with the FortiOS / RouterOS version it was written
  for and "sahada doğrula" where the repository has no format sample.
- `docs/KURULUM.md` / `docs/INSTALL.md`: `[[webhooks]]`, the secret, the firewall rule, "Test
  gönder".
- `docs/LISANS.md` / `docs/LICENSING.md`: the `security_rules` module (what stays in Temel).
- KVKK note for R09, R13, R14 with the legal-review recommendation.

---

## 3. Tests (the minimum; packages add more)

| Id | Package | Test |
|---|---|---|
| T-A1 | W6-A | `test_rules_registry_complete`: every id R01–R14, R16–R19 is in the registry exactly once, has TR/EN name, title, advice, at least one input, and a positive and a negative case in `worker/tests/rules/cases.py`; fails naming the missing id |
| T-A2 | W6-A | watermark: a slice is evaluated once; a failing slice rolls back and is evaluated again; D12 (no alert for data older than the switch-on) |
| T-A3 | W6-A | dedup, cooldown, `after_close`, evidence cap, the condition kind's auto-resolve |
| T-A4 | W6-A | settings validation (every error with its `field`), a stored invalid value falls back to the default |
| T-A5 | W6-A | dry run = live run on the same data; dry run writes nothing (row counts before/after) |
| T-A6 | W6-A | perf guard §2.1.7 (`perf` marker) |
| T-A7 | W6-A | bound parameters: values with quotes, semicolons and `%` in every list parameter change nothing but the match |
| T-A8 | W6-A | API: permissions, module lock, audit `rule.update` before/after, assignment, evidence, `alert.person_view` |
| T-R  | W6-B…E | per rule: positive case (alert with the right key, count, evidence refs that `/raw` can open), negative case just below the threshold, the cooldown case; §0.4 format tests |
| T-H1 | W6-H | delivery: queue, success, retry schedule, permanent 4xx, 24-h failure → `R20-webhook`, resolution; no backfill |
| T-H2 | W6-H | payloads pinned (`test_webhook_payload_shapes`), HMAC verified with a fixed secret and timestamp; no person name, no raw line, no secret in any payload or `last_error` |
| T-H3 | W6-H | config validation (https only, names, limits); TLS verification cannot be switched off |
| T-H4 | W6-H | firewall: `ARCILOGGER Out Webhook <name>` in strict mode (Windows CI) |
| T-F  | W6-F | unit tests of the editor model and the alert additions, axe on every new screen, e2e with the captured samples |
| T-I  | W6-I | every scenario produces the lines its rule fires on (the loadgen test runs the rule) |

---

## 4. Work packages

### 4.1 Branch, gate and ownership rules

- Start from `origin/wave-6`, open the pull request into `wave-6`.
- `CLAUDE.md`'s gates before every push; API or schema changes regenerate the API contract and
  the samples.
- One owner per file (§4.3). A rule package edits only its rule module, its tests and its
  cases in `worker/tests/rules/cases.py` (append-only, one block per package).

### 4.2 Coupling points

- **Day 1 of W6-A** (first pull request, merged first): `model.py`, `registry.py`, `texts.py`,
  the empty rule modules, `cases.py` with the shape of a case, the migration `v0024_rules`, the
  settings keys, and `RuleContext`. The rule packages start from the contract and rebase on
  this when it lands.
- W6-F works from §2.6's JSON with mocks until W6-A's API lands, then from the captured
  samples.
- W6-H needs `ALERT_DELIVERY_KINDS` `webhook` (W6-A day 1) and the module key (W6-A day 1).

### 4.3 File ownership (main files; the issues list every file)

| Package | Owns |
|---|---|
| W6-A | `worker/.../rules/{model,registry,engine,settings,dryrun,texts}.py`, `worker/.../service.py` (the `rules.run` job), `db/.../models.py` (the §2.5 hunks), `db/.../migrations/v0024_rules.py`, `db/.../settings.py` and `settings_meta.py` (the `rules.*` hunks), `core/.../license/modules.py` (the module entry), `api/.../routers/rules.py` (new), `api/.../routers/alerts.py` (the §2.6.2 hunks), `worker/.../alerts/store.py` (assignment), `worker/tests/rules/` (framework and `cases.py` shape) |
| W6-B | `worker/.../rules/network.py`, its tests and cases |
| W6-C | `worker/.../rules/access.py`, its tests and cases |
| W6-D | `worker/.../rules/threats.py`, its tests and cases |
| W6-E | `worker/.../rules/people.py`, its tests and cases |
| W6-F | `web/src/pages/settings/rules/` (new), `web/src/pages/security/` (the additions), the webhooks view, nav, locale keys, hooks, e2e mocks |
| W6-H | `worker/.../alerts/webhooks.py` (new), `worker/.../alerts/notify.py` (D11 hunk), `worker/.../service.py` (the `alerts.webhooks` job hunk), `core/.../config.py` (`WebhookConfig`), `core/.../admin/probe.py` (`webhook`), `api/.../routers/webhooks.py` (new), `deploy/windows/scripts/Set-ArciFirewall.ps1` (webhook rules), `deploy/windows/arcilogger.example.toml` |
| W6-I | `tools/loadgen/`, `docs/UYARILAR.md`, `docs/ALERTS.md`, `docs/DEVICE-SETUP.md`, the §2.8 hunks of the install and licence docs |

### 4.4 The packages and owners

| Package | Owner | Issue title |
|---|---|---|
| W6-A | Tayfun (`tayfunglytltd`) | Rule engine, schema, settings, rules API, assignment and evidence (Öncelik: ilk) |
| W6-B | info (`infoglytltd`) | Network rules R01, R02, R08, R10, R19 |
| W6-C | Saray (`sarayglytltd`) | Access and device rules R03, R04, R11, R12, R16 |
| W6-D | arman-glyt (`armanglytltd`) | Threat and health rules R05, R06, R07, R17, R18 |
| W6-E | arman-glyt (`armanglytltd`), after W6-D | People rules R09, R13, R14 |
| W6-F | Gülşah (`gulsahglytltd`) | Web: rule editor, Security page additions, webhooks view |
| W6-H | info (`infoglytltd`), after W6-B | Webhooks back end |
| W6-I | Saray (`sarayglytltd`), after W6-C | Load generator scenarios and docs |
| Follow-ups | #217 info after W6-H; #222 Tayfun after W6-A; #227 Gülşah after W6-F | as written on the issues |

### 4.5 Merge order

1. W6-A day 1 (framework, schema, settings, module).
2. Rule packages W6-B, W6-C, W6-D in any order; W6-E after W6-D.
3. W6-A rest (API, dry run, assignment, evidence, perf guard) — may merge before or between 2.
4. W6-H after W6-A day 1.
5. W6-F parts as their back end lands.
6. W6-I last (docs describe the merged behaviour; the loadgen scenarios may merge earlier).
7. The manager: version 0.5.0, CHANGELOG, `wave-6` → `main`.

---

## 5. Implementation notes

- A rule's SQL is written with SQLAlchemy Core (`select`, `func.count(distinct(...))`,
  `literal` never with user values), like `worker/src/arcilogger_worker/usage/`.
- Internal / external: `srcip <<= ANY(:internal_networks::cidr[])`.
- Site time (R04, R14): `arcilogger_core.timeutil` site zone; the work-hours helper of
  `arcilogger_worker.usage` (W5-K) decides work / break / off — breaks count as work hours for
  R04 (an admin login during lunch is not "mesai dışı").
- The person of an address (R09, R13, R14) comes from the existing attribution, at the row's
  device time; no new attribution code.
- Never log a parameter list or an evidence value at INFO; the worker log names the rule, the
  slice and the counts.

---

## 6. Manager clarifications

(Answers to questions on the issues are appended here.)

---

## 7. Owner questions (the defaults ship)

| # | Question | Default (§0.2) |
|---|---|---|
| Q1 | R01–R19 in 5651 Temel, or a separate paid module? | separate module `security_rules`; R15, R20 stay free |
| Q2 | Only Yönetici sees alerts, or a separate "Güvenlik" role? | only Yönetici |
| Q3 | Person names on the Security page for R09, R13, R14? | only for `usage.person`, audited; legal review |
| Q4 | Webhook targets set in the browser or on the server only? External cloud (Teams, Slack)? | server only; cloud only when the administrator writes it |
| Q5 | R04, R14 use the usage module's work hours even without its licence? | yes |
| Q6 | Custom rules in Wave 6? | no |
| Q7 | Anonymised sample lines from the site's FortiGate (IPS, AV, app control, admin login, config change, pool empty, perf)? | without them the rules of §0.4 ship "sahada doğrula" |

---

## Deviations

### W6-C (#245): R11 matches the device's insert time, not `first_seen`

- **Contract:** §2.2.2 R11, "a `devices` row whose `first_seen` is in the slice".
- **Implemented:** a `devices` row whose `created_at` (the server's insert time) is in the slice; `first_seen` is shown in the alert's values.
- **Why:** `first_seen` is the **device time** of the first DHCP or ARP sighting. The lease jobs write the row later, after their own lag. A device whose row is inserted after `rules.run` moved its watermark past that `first_seen` would never alert. `created_at` is written once, at the insert, by the server's clock. That fits D8 (receive time, not device time) and D12 (devices that existed when the rule started never alert, with no extra seeding).
- **Limit:** a lease-job transaction that runs longer than `rules.lag_seconds` (120 s) can commit rows with a `created_at` behind the watermark. The default lag covers the normal job; a full rebuild of old days creates no new devices worth an alert.
