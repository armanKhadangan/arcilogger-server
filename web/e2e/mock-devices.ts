/**
 * The mocked devices, persons, departments, VLAN and CSV import/export endpoints of the E2E tests
 * (docs/MVP-CONTRACTS.md section 11). They start from the answers captured from the real API
 * (`samples/devices.json`) and keep the state a browser session changes, with the API's rules:
 *
 * - reads `devices.read`, changes `devices.manage`; devices, persons and imports also need the
 *   accepted KVKK notice (`privacy_notice_required`); departments, VLANs and templates do not;
 * - `GET /devices`: `q` (part of the MAC — also without separators —, a host name, the last IP or
 *   the device name; Turkish letters folded), `unknown` (no owner, not infrastructure), `type`,
 *   `person_id`, `vlan`, `randomized`, `infrastructure`, `sort`, `order`, `limit` 1…500, `offset`;
 * - `POST /devices/assign`: all or nothing; the history is appended to, never rewritten
 *   (409 `assignment_in_future` beyond a 2-minute slack — within it the start is now —, 409
 *   `assignment_overlap` before the current assignment's start or the end of the last one), 409
 *   `person_inactive`, 404 `device_not_found`, 422 for a note on an unassign; a device the person
 *   already holds is not counted in `updated`;
 * - `POST /vlans`, `PATCH /vlans` (`subnet` names the VLAN, only the fields sent change,
 *   `new_subnet` moves it), `DELETE /vlans?subnet=`: IPv4 networks with their prefix length and
 *   the host bits zero (422 otherwise), 409 `vlan_exists` / `vlan_id_taken` / `dhcp_server_taken`;
 *   a device's VLAN is that of `last_vlan`, else the most specific subnet holding `last_ip`;
 * - `POST /imports/{kind}`: the multipart field `file`, a dry run unless `dry_run=false`, an apply
 *   writes every line or none; whole-file problems are API errors (`csv_header`, `upload_invalid`);
 * - CSV exports answer the captured files (the filters are recorded in the call).
 *
 * Error texts are the API's message tables (`routers/devices.py`, `csvimport.py`) or the captured
 * refusals. The mock's clock is the sample day's 15:00 (the E2E browser clock, `useSampleClock`).
 */

import type {
  AssignmentOut,
  AssignRequest,
  DepartmentCreate,
  DepartmentOut,
  DepartmentRef,
  DepartmentUpdate,
  DeviceOut,
  DeviceUpdate,
  ImportLineError,
  ImportResult,
  LeaseBrief,
  PersonCreate,
  PersonOut,
  PersonUpdate,
  VlanCreate,
  VlanOut,
  VlanUpdate,
} from '../src/lib/api-types';

import type { MockUser, RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { copy, DEVICES, type CsvSample, type ErrorSample } from './samples';

/** What the devices mock needs from the main mock. */
export interface DevicesHost extends MockHost {
  privacyRequired(account: MockUser): boolean;
}

/** The fixed "now" of the mock: 15:00 site time on the sample day. */
export const DEVICES_NOW = Date.parse(`${DEVICES.meta.site_today}T15:00:00+03:00`);
/** `valid_from` may be this far ahead of the server's clock. */
const FUTURE_SLACK_MS = 2 * 60_000;

const SORTS = ['last_seen', 'first_seen', 'mac', 'last_ip', 'name'] as const;
const PREVIEW_ROWS = 50;
const PERSON_COLUMNS = ['name', 'department', 'title', 'email', 'employee_no', 'active', 'notes'];
/** Columns of the persons export an import accepts: `id` names the person, the rest is ignored. */
const PERSON_EXPORT_ONLY = ['id', 'device_count', 'created_at', 'updated_at'];
const ASSIGNMENT_COLUMNS = ['mac', 'employee_no', 'person_name', 'valid_from', 'note'];
const BOM = String.fromCharCode(0xfeff);

/** `arcilogger_api.csvimport` line messages (TR, EN). */
type LineKey =
  | 'required'
  | 'email'
  | 'department'
  | 'device_unknown'
  | 'person_missing'
  | 'person_unknown_no'
  | 'person_unknown_id'
  | 'id_mismatch'
  | 'person_unknown_name'
  | 'person_inactive'
  | 'valid_from'
  | 'in_future'
  | 'overlap';
const LINE: Record<LineKey, [string, string]> = {
  required: ['{column} boş olamaz.', '{column} must not be empty.'],
  email: ['E-posta adresi geçersiz ({value}).', 'The e-mail address is invalid ({value}).'],
  department: [
    "'{value}' adlı departman yok; önce Departmanlar listesine ekleyin.",
    "There is no department '{value}'; add it to the departments first.",
  ],
  device_unknown: [
    '{value} cihazı henüz görülmedi (cihaz listesinde yok); cihaz ağa bağlanıp göründükten sonra tekrar içe aktarın.',
    'Device {value} has not been seen yet (it is not in the device list); import it again once the device has been on the network.',
  ],
  person_missing: [
    'Kişi belirtilmemiş: employee_no veya person_name gerekli.',
    'No person given: employee_no or person_name is required.',
  ],
  person_unknown_no: [
    'employee_no {value} olan kişi yok.',
    'There is no person with employee_no {value}.',
  ],
  person_unknown_id: [
    'id {value} olan kişi yok; dışa aktarılan dosyanın id sütununu değiştirmeyin (yeni kişi için boş bırakın).',
    'There is no person with id {value}; do not change the id column of an exported file (leave it empty for a new person).',
  ],
  id_mismatch: [
    'employee_no {number} başka bir kişiye ait (id {other}); bu satır id {value} kişisinin.',
    'employee_no {number} belongs to another person (id {other}); this line is the person with id {value}.',
  ],
  person_unknown_name: ["'{value}' adlı kişi yok.", "There is no person named '{value}'."],
  person_inactive: [
    "'{value}' pasif; pasif bir kişiye cihaz atanamaz.",
    "'{value}' is inactive; devices cannot be assigned to an inactive person.",
  ],
  valid_from: [
    'valid_from okunamadı ({value}); örnek: 2026-10-01T09:00:00+03:00 veya 01.10.2026 09:00.',
    'valid_from cannot be read ({value}); example: 2026-10-01T09:00:00+03:00 or 01.10.2026 09:00.',
  ],
  in_future: [
    'valid_from gelecekte olamaz ({value}).',
    'valid_from must not be in the future ({value}).',
  ],
  overlap: [
    'valid_from ({value}), {mac} cihazının önceki atamasından ({limit}) önce; atama geçmişi değiştirilemez, yalnızca ileriye doğru eklenir.',
    'valid_from ({value}) is before the previous assignment of {mac} ({limit}); the assignment history cannot be rewritten, only extended.',
  ],
};

/** `routers/devices.py` messages (TR, EN). */
const DEVICE: Record<'device_not_found' | 'assignment_overlap' | 'csv_empty', [string, string]> = {
  device_not_found: ['Cihaz bulunamadı: {macs}.', 'Device not found: {macs}.'],
  csv_empty: [
    'Dosyada başlık satırı ve en az bir veri satırı olmalıdır.',
    'The file needs a header line and at least one data row.',
  ],
  assignment_overlap: [
    'Atama başlangıcı, {mac} cihazının önceki atamasından ({limit}) önce olamaz: atama geçmişi değiştirilemez, yalnızca ileriye doğru eklenir.',
    "The start of the assignment must not be before {mac}'s previous assignment ({limit}): the assignment history cannot be rewritten, only extended.",
  ],
};

type PersonRecord = Omit<PersonOut, 'device_count'>;

function fill(text: string, params: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (_, key: string) => params[key] ?? `{${key}}`);
}

function error(
  status: number,
  code: string,
  [tr, en]: [string, string],
  params: Record<string, string> = {},
  details?: unknown,
): Answer {
  return [
    status,
    {
      error: {
        code,
        message_tr: fill(tr, params),
        message_en: fill(en, params),
        ...(details === undefined ? {} : { details }),
      },
    },
  ];
}

function sample(refusal: ErrorSample | undefined): Answer {
  if (!refusal) throw new Error('missing error sample');
  return [refusal.status, copy(refusal.body)];
}

function lineError(
  line: number,
  key: LineKey,
  params: Record<string, string> = {},
): ImportLineError {
  const [tr, en] = LINE[key];
  return { line, message_tr: fill(tr, params), message_en: fill(en, params) };
}

/** `12.10.2026 09:00:00` in site time (the API's `format_datetime`). */
function siteText(iso: string): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Europe/Istanbul',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(new Date(iso))
      .map((part) => [part.type, part.value]),
  );
  return `${parts.day}.${parts.month}.${parts.year} ${parts.hour}:${parts.minute}:${parts.second}`;
}

/**
 * `valid_from` of an assignment file (`csvimport`): ISO-8601 (without offset: site time) or
 * `01.10.2026 09:00[:00]` / `01.10.2026`; `null` when it cannot be read. Turkey keeps +03:00.
 */
function parseValidFrom(text: string): number | null {
  const local = /^(\d{2})\.(\d{2})\.(\d{4})(?: (\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(text);
  const iso = local
    ? `${local[3]}-${local[2]}-${local[1]}T${local[4] ?? '00'}:${local[5] ?? '00'}:${local[6] ?? '00'}+03:00`
    : /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(text)
      ? `${text}+03:00`
      : /^\d{4}-\d{2}-\d{2}T/.test(text)
        ? text
        : '';
  const time = Date.parse(iso);
  return Number.isNaN(time) ? null : time;
}

function isoNow(): string {
  return new Date(DEVICES_NOW).toISOString();
}

/** Case-insensitive with Turkish letters folded (`arcilogger_api.textfold`). */
function fold(text: string | null | undefined): string {
  return (text ?? '')
    .toLocaleLowerCase('tr')
    .replace(
      /[çğıöşü]/g,
      (char) => ({ ç: 'c', ğ: 'g', ı: 'i', ö: 'o', ş: 's', ü: 'u' })[char] ?? char,
    )
    .normalize('NFD')
    .replace(/\p{M}/gu, '');
}

/** An IPv4 address as a sortable number (empty values last). */
function ipKey(ip: string | null): number {
  const parts = (ip ?? '').split('.').map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part))) return Infinity;
  return parts.reduce((sum, part) => sum * 256 + part, 0);
}

/** A dotted IPv4 address as a number, `null` when it is not one. */
function ipv4Number(text: string): number | null {
  const parts = text.split('.');
  if (parts.length !== 4 || parts.some((part) => !/^(0|[1-9]\d{0,2})$/.test(part))) return null;
  const numbers = parts.map(Number);
  return numbers.some((part) => part > 255) ? null : numbers.reduce((sum, n) => sum * 256 + n, 0);
}

/** `routers/vlans.py` (IPv4 here): an address with its prefix length, host bits zero. */
function networkOf(text: string): string | null {
  const [address = '', prefix = '', ...rest] = text.trim().split('/');
  const value = ipv4Number(address);
  if (value === null || rest.length > 0 || !/^\d{1,2}$/.test(prefix) || Number(prefix) > 32)
    return null;
  return value % 2 ** (32 - Number(prefix)) === 0 ? `${address}/${String(Number(prefix))}` : null;
}

/** The file field of a multipart body: `{name, text}`, `null` when there is none. */
function multipartFile(call: RecordedCall): { name: string; text: string } | null {
  const boundary = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(call.contentType ?? '');
  const marker = boundary?.[1] ?? boundary?.[2];
  if (!marker || !call.raw) return null;
  for (const part of call.raw.split(`--${marker}`)) {
    const split = part.indexOf('\r\n\r\n');
    if (split < 0) continue;
    const head = part.slice(0, split);
    if (!/name="file"/i.test(head)) continue;
    const name = /filename="([^"]*)"/i.exec(head)?.[1] ?? 'upload.csv';
    return { name, text: part.slice(split + 4).replace(/\r\n$/, '') };
  }
  return null;
}

/** Rows of a CSV text (`;` or `,`, detected from the header; quotes as in RFC 4180). */
function parseCsv(text: string): string[][] {
  const body = text.startsWith(BOM) ? text.slice(1) : text;
  const firstLine = body.split(/\r?\n/, 1)[0] ?? '';
  const separator = firstLine.includes(';') ? ';' : ',';
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let index = 0; index < body.length; index += 1) {
    const char = body.charAt(index);
    if (quoted) {
      if (char === '"' && body.charAt(index + 1) === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === separator) {
      row.push(field);
      field = '';
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && body.charAt(index + 1) === '\n') index += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else field += char;
  }
  if (field !== '' || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((cells) => cells.some((value) => value.trim() !== ''));
}

export class DevicesMock {
  readonly devices = new Map<string, DeviceOut>();
  readonly persons = new Map<number, PersonRecord>();
  readonly departments = new Map<number, Omit<DepartmentOut, 'person_count' | 'vlan_ids'>>();
  readonly vlans: VlanOut[];
  /** Every assignment row, newest first. */
  readonly assignments: AssignmentOut[];
  readonly leases: Record<string, LeaseBrief[]>;
  private nextAssignment: number;
  private nextPerson: number;
  private nextDepartment: number;

  constructor() {
    for (const device of DEVICES.list.items) this.devices.set(device.mac, copy(device));
    for (const person of DEVICES.persons.items) {
      const record: PersonRecord & { device_count?: number } = copy(person);
      delete record.device_count;
      this.persons.set(person.id, record);
    }
    for (const department of DEVICES.departments)
      this.departments.set(department.id, {
        id: department.id,
        name: department.name,
        notes: department.notes,
      });
    this.vlans = copy(DEVICES.vlans);
    const rows = new Map<number, AssignmentOut>();
    for (const detail of [DEVICES.detail, DEVICES.detail_phone, DEVICES.person_detail])
      for (const row of detail.assignments) rows.set(row.id, copy(row));
    this.assignments = [...rows.values()].sort(
      (a, b) => Date.parse(b.valid_from) - Date.parse(a.valid_from),
    );
    this.leases = {
      [DEVICES.detail.mac]: copy(DEVICES.detail.recent_leases),
      [DEVICES.detail_phone.mac]: copy(DEVICES.detail_phone.recent_leases),
    };
    this.nextAssignment = Math.max(0, ...rows.keys()) + 1;
    this.nextPerson = Math.max(...this.persons.keys()) + 1;
    this.nextDepartment = Math.max(...this.departments.keys()) + 1;
  }

  answer(call: RecordedCall, host: DevicesHost): Answer | null {
    const { method, path } = call;
    let match: RegExpExecArray | null;
    if (path === '/devices' && method === 'GET') return this.listDevices(call, host);
    if (path === '/devices/export.csv' && method === 'GET')
      return this.csv(call, host, DEVICES.devices_csv);
    if (path === '/devices/assign' && method === 'POST') return this.assign(call, host);
    if ((match = /^\/devices\/([^/]+)$/.exec(path))) {
      const mac = decodeURIComponent(match[1] ?? '');
      if (method === 'GET') return this.getDevice(call, host, mac);
      if (method === 'PATCH') return this.updateDevice(call, host, mac);
    }
    if (path === '/persons' && method === 'GET') return this.listPersons(call, host);
    if (path === '/persons' && method === 'POST') return this.createPerson(call, host);
    if (path === '/persons/export.csv' && method === 'GET')
      return this.csv(call, host, DEVICES.persons_csv);
    if ((match = /^\/persons\/(\d+)$/.exec(path))) {
      const id = Number(match[1]);
      if (method === 'GET') return this.getPerson(call, host, id);
      if (method === 'PATCH') return this.updatePerson(call, host, id);
    }
    if (path === '/departments' && method === 'GET') return this.listDepartments(call, host);
    if (path === '/departments' && method === 'POST') return this.createDepartment(call, host);
    if ((match = /^\/departments\/(\d+)$/.exec(path)) && method === 'PATCH')
      return this.updateDepartment(call, host, Number(match[1]));
    if (path === '/vlans' && method === 'GET') return this.listVlans(call, host);
    if (path === '/vlans' && method === 'POST') return this.createVlan(call, host);
    if (path === '/vlans' && method === 'PATCH') return this.updateVlan(call, host);
    if (path === '/vlans' && method === 'DELETE') return this.deleteVlan(call, host);
    if ((match = /^\/imports\/(persons|assignments)(\/template\.csv)?$/.exec(path))) {
      const kind = match[1] as 'persons' | 'assignments';
      if (match[2] && method === 'GET') return this.template(call, host, kind);
      if (!match[2] && method === 'POST') return this.importCsv(call, host, kind);
    }
    return null;
  }

  // -- access -----------------------------------------------------------------------------------

  /** `permission` and, for person data, the accepted KVKK notice. */
  private allowed(
    call: RecordedCall,
    host: DevicesHost,
    permission: 'devices.read' | 'devices.manage',
    personData = true,
  ): MockUser | [number, unknown] {
    const account = host.requirePermission(call, permission);
    if (Array.isArray(account)) return account;
    if (personData && host.privacyRequired(account) && !account.privacy_accepted)
      return host.error(403, 'privacy_notice_required');
    return account;
  }

  // -- derived values ---------------------------------------------------------------------------

  private departmentRef(id: number | null | undefined): DepartmentRef | null {
    const found = id === null || id === undefined ? undefined : this.departments.get(id);
    return found ? { id: found.id, name: found.name } : null;
  }

  private personOut(record: PersonRecord): PersonOut {
    const count = [...this.devices.values()].filter((item) => item.person?.id === record.id).length;
    return { ...copy(record), device_count: count };
  }

  /** The owner's department, else the VLAN's; both null when neither exists. */
  private refresh(device: DeviceOut): void {
    const owner = device.person ? this.persons.get(device.person.id) : undefined;
    if (owner) device.person = { id: owner.id, name: owner.name };
    const vlan = this.vlanOf(device);
    device.vlan_name = vlan?.name_tr ?? null;
    const own = this.departmentRef(owner?.department?.id);
    if (own) {
      device.department = own;
      device.department_source = 'person';
      return;
    }
    device.department = vlan?.department ? copy(vlan.department) : null;
    device.department_source = device.department ? 'vlan' : null;
  }

  /** The VLAN of `last_vlan`, else the most specific (IPv4) subnet holding `last_ip`. */
  private vlanOf(device: DeviceOut): VlanOut | undefined {
    const byId = this.vlans.find(
      (item) => item.vlan_id !== null && item.vlan_id === device.last_vlan,
    );
    if (byId || device.last_ip === null) return byId;
    const address = ipv4Number(device.last_ip);
    if (address === null) return undefined;
    let best: { vlan: VlanOut; prefix: number } | undefined;
    for (const vlan of this.vlans) {
      const [base = '', bits = ''] = vlan.subnet.split('/');
      const network = ipv4Number(base);
      const prefix = Number(bits);
      if (network === null || (best && best.prefix >= prefix)) continue;
      const size = 2 ** (32 - prefix);
      if (address - (address % size) === network) best = { vlan, prefix };
    }
    return best?.vlan;
  }

  private refreshAll(): void {
    for (const record of this.persons.values())
      record.department = this.departmentRef(record.department?.id);
    for (const vlan of this.vlans) vlan.department = this.departmentRef(vlan.department?.id);
    for (const device of this.devices.values()) this.refresh(device);
  }

  // -- devices ----------------------------------------------------------------------------------

  private listDevices(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.read');
    if (Array.isArray(account)) return account;
    const q = call.query;
    const limit = Number(q.get('limit') ?? 50);
    const offset = Number(q.get('offset') ?? 0);
    const sort = q.get('sort') ?? 'last_seen';
    const order = q.get('order') ?? 'desc';
    if (
      !Number.isInteger(limit) ||
      limit < 1 ||
      limit > 500 ||
      !Number.isInteger(offset) ||
      offset < 0 ||
      !(SORTS as readonly string[]).includes(sort) ||
      (order !== 'asc' && order !== 'desc')
    )
      return host.error(422, 'validation_error');
    const text = fold(q.get('q')?.trim());
    const bare = text.replace(/[:.-]/g, '');
    const flag = (name: string) => (q.has(name) ? q.get(name) === 'true' : null);
    const unknown = flag('unknown');
    const randomized = flag('randomized');
    const infrastructure = flag('infrastructure');
    const type = q.get('type');
    const vlan = q.has('vlan') ? Number(q.get('vlan')) : null;
    const personId = q.has('person_id') ? Number(q.get('person_id')) : null;
    const rows = [...this.devices.values()]
      .filter(
        (device) =>
          !text ||
          [device.name, device.last_ip, ...device.hostnames].some((value) =>
            fold(value).includes(text),
          ) ||
          device.mac.includes(text) ||
          (bare !== '' && device.mac.replace(/:/g, '').includes(bare)),
      )
      .filter((device) => unknown !== true || (device.person === null && !device.infrastructure))
      .filter((device) => randomized === null || device.randomized === randomized)
      .filter((device) => infrastructure === null || device.infrastructure === infrastructure)
      .filter((device) => !type || device.type === type)
      .filter((device) => vlan === null || device.last_vlan === vlan)
      .filter((device) => personId === null || device.person?.id === personId);
    const key = (device: DeviceOut): string | number | null => {
      if (sort === 'last_ip') return device.last_ip ? ipKey(device.last_ip) : null;
      if (sort === 'mac') return device.mac;
      if (sort === 'name') return device.name ? fold(device.name) : null;
      const value = sort === 'first_seen' ? device.first_seen : device.last_seen;
      return value ? Date.parse(value) : null;
    };
    rows.sort((a, b) => {
      const left = key(a);
      const right = key(b);
      if (left === null || right === null) return left === right ? 0 : left === null ? 1 : -1;
      const compared =
        typeof left === 'number' && typeof right === 'number'
          ? left - right
          : String(left).localeCompare(String(right), 'tr');
      return (order === 'asc' ? compared : -compared) || a.mac.localeCompare(b.mac);
    });
    return [
      200,
      { items: copy(rows.slice(offset, offset + limit)), total: rows.length, limit, offset },
    ];
  }

  private getDevice(call: RecordedCall, host: DevicesHost, mac: string): Answer {
    const account = this.allowed(call, host, 'devices.read');
    if (Array.isArray(account)) return account;
    const device = this.devices.get(mac);
    if (!device) return error(404, 'device_not_found', DEVICE.device_not_found, { macs: mac });
    return [
      200,
      {
        ...copy(device),
        assignments: copy(this.assignments.filter((item) => item.mac === mac)),
        recent_leases: copy(this.leases[mac] ?? []).slice(0, 20),
      },
    ];
  }

  private updateDevice(call: RecordedCall, host: DevicesHost, mac: string): Answer {
    const account = this.allowed(call, host, 'devices.manage');
    if (Array.isArray(account)) return account;
    const device = this.devices.get(mac);
    if (!device) return error(404, 'device_not_found', DEVICE.device_not_found, { macs: mac });
    const body = call.body as DeviceUpdate;
    if (body.name !== undefined) device.name = body.name;
    if (body.type !== undefined) device.type = body.type;
    if (body.infrastructure !== undefined && body.infrastructure !== null)
      device.infrastructure = body.infrastructure;
    if (body.notes !== undefined) device.notes = body.notes;
    if (body.tags !== undefined) device.tags = [...new Set(body.tags ?? [])];
    return [200, copy(device)];
  }

  /** The earliest allowed start for `mac`: its current assignment's start or the last end. */
  private earliest(mac: string): number | null {
    const rows = this.assignments.filter((item) => item.mac === mac);
    const times = rows.map((row) => Date.parse(row.valid_to ?? row.valid_from));
    return times.length ? Math.max(...times) : null;
  }

  private assign(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as AssignRequest;
    const macs = [...new Set(body.macs)];
    // An unassign opens no assignment to keep a note on: the API refuses one.
    if (body.person_id === null && body.note?.trim())
      return host.error(422, 'validation_error', [
        {
          loc: ['body', 'note'],
          type: 'value_error',
          msg: 'an unassign (person_id null) takes no note',
        },
      ]);
    let person: PersonRecord | null = null;
    if (body.person_id !== null) {
      const found = this.persons.get(body.person_id);
      if (!found)
        return host.error(422, 'validation_error', [
          { loc: ['body', 'person_id'], type: 'value_error', msg: 'unknown person' },
        ]);
      if (!found.active) return sample(DEVICES.errors.person_inactive);
      person = found;
    }
    const missing = macs.filter((mac) => !this.devices.has(mac));
    if (missing.length > 0)
      return error(
        404,
        'device_not_found',
        DEVICE.device_not_found,
        { macs: missing.join(', ') },
        {
          macs: missing,
        },
      );
    const requested = body.valid_from ? Date.parse(body.valid_from) : DEVICES_NOW;
    // Up to the slack ahead of the server clock counts as now and is stored as now.
    const start = Math.min(requested, DEVICES_NOW);
    const changing = macs.filter(
      (mac) => (this.devices.get(mac)?.person?.id ?? null) !== (person?.id ?? null),
    );
    const problems = changing.flatMap((mac) => {
      if (requested > DEVICES_NOW + FUTURE_SLACK_MS)
        return [{ mac, reason: 'in_future', earliest: null as string | null }];
      const limit = this.earliest(mac);
      return limit !== null && start < limit
        ? [{ mac, reason: 'overlap', earliest: new Date(limit).toISOString() }]
        : [];
    });
    const overlap = problems.find((problem) => problem.reason === 'overlap');
    if (overlap?.earliest)
      return error(
        409,
        'assignment_overlap',
        DEVICE.assignment_overlap,
        { mac: overlap.mac, limit: siteText(overlap.earliest) },
        problems,
      );
    if (problems.length > 0) {
      const refusal = DEVICES.errors.assignment_in_future;
      if (!refusal) throw new Error('missing error sample');
      return [refusal.status, { error: { ...copy(refusal.body.error), details: problems } }];
    }
    const at = new Date(start).toISOString().replace('.000Z', 'Z');
    for (const mac of changing) {
      const device = this.devices.get(mac);
      if (!device) continue;
      const current = this.assignments.find((item) => item.mac === mac && item.valid_to === null);
      if (current) current.valid_to = at;
      if (person) {
        this.assignments.unshift({
          id: this.nextAssignment++,
          mac,
          person: { id: person.id, name: person.name },
          valid_from: at,
          valid_to: null,
          assigned_by: account.username,
          note: body.note ?? null,
          backfilled: false,
        });
        device.person = { id: person.id, name: person.name };
        device.assigned_since = at;
      } else {
        device.person = null;
        device.assigned_since = null;
      }
      this.refresh(device);
    }
    return [
      200,
      { updated: changing.length, devices: macs.map((mac) => copy(this.devices.get(mac))) },
    ];
  }

  /** A captured CSV export or template, as the API sent it. */
  private csv(call: RecordedCall, host: DevicesHost, file: CsvSample, personData = true): Answer {
    const account = this.allowed(call, host, 'devices.read', personData);
    if (Array.isArray(account)) return account;
    return [
      200,
      null,
      {
        body: Buffer.from(file.text, 'utf8'),
        contentType: file.headers['content-type'] ?? 'text/csv; charset=utf-8',
        headers: {
          'Content-Disposition': file.headers['content-disposition'] ?? 'attachment',
          'Cache-Control': 'no-store',
        },
      },
    ];
  }

  // -- persons ----------------------------------------------------------------------------------

  private listPersons(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.read');
    if (Array.isArray(account)) return account;
    const q = call.query;
    const limit = Number(q.get('limit') ?? 50);
    const offset = Number(q.get('offset') ?? 0);
    if (!Number.isInteger(limit) || limit < 1 || limit > 500 || offset < 0)
      return host.error(422, 'validation_error');
    const text = fold(q.get('q')?.trim());
    const active = q.has('active') ? q.get('active') === 'true' : null;
    const department = q.has('department_id') ? Number(q.get('department_id')) : null;
    const rows = [...this.persons.values()]
      .filter(
        (person) =>
          !text ||
          [person.name, person.email, person.employee_no, person.title].some((value) =>
            fold(value).includes(text),
          ),
      )
      .filter((person) => active === null || person.active === active)
      .filter((person) => department === null || person.department?.id === department)
      .sort((a, b) => a.name.localeCompare(b.name, 'tr'))
      .map((person) => this.personOut(person));
    return [200, { items: rows.slice(offset, offset + limit), total: rows.length, limit, offset }];
  }

  private getPerson(call: RecordedCall, host: DevicesHost, id: number): Answer {
    const account = this.allowed(call, host, 'devices.read');
    if (Array.isArray(account)) return account;
    const person = this.persons.get(id);
    if (!person) return host.error(404, 'not_found');
    return [
      200,
      {
        ...this.personOut(person),
        devices: copy([...this.devices.values()].filter((device) => device.person?.id === id)),
        assignments: copy(this.assignments.filter((item) => item.person?.id === id)),
      },
    ];
  }

  private employeeNoTaken(number: string | null | undefined, except: number | null): Answer | null {
    const taken =
      Boolean(number) &&
      [...this.persons.values()].some(
        (person) => person.employee_no === number && person.id !== except,
      );
    if (!taken) return null;
    const refusal = DEVICES.errors.employee_no_taken;
    if (!refusal) throw new Error('missing error sample');
    const body = copy(refusal.body);
    body.error.message_tr = body.error.message_tr.replace(/\(\d+\)/, `(${number ?? ''})`);
    body.error.message_en = body.error.message_en.replace(/\(\d+\)/, `(${number ?? ''})`);
    return [refusal.status, body];
  }

  private createPerson(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as PersonCreate;
    if (!body.name.trim()) return host.error(422, 'validation_error');
    const taken = this.employeeNoTaken(body.employee_no, null);
    if (taken) return taken;
    const now = isoNow();
    const record: PersonRecord = {
      id: this.nextPerson++,
      name: body.name.trim(),
      department: this.departmentRef(body.department_id),
      title: body.title ?? null,
      email: body.email ?? null,
      employee_no: body.employee_no ?? null,
      active: body.active ?? true,
      notes: body.notes ?? null,
      created_at: now,
      updated_at: now,
    };
    this.persons.set(record.id, record);
    return [201, this.personOut(record)];
  }

  private updatePerson(call: RecordedCall, host: DevicesHost, id: number): Answer {
    const account = this.allowed(call, host, 'devices.manage');
    if (Array.isArray(account)) return account;
    const person = this.persons.get(id);
    if (!person) return host.error(404, 'not_found');
    const body = call.body as PersonUpdate;
    if (body.name === null || body.active === null) return host.error(422, 'validation_error');
    if (body.employee_no !== undefined) {
      const taken = this.employeeNoTaken(body.employee_no, id);
      if (taken) return taken;
    }
    if (body.name !== undefined) person.name = body.name.trim();
    if (body.department_id !== undefined)
      person.department = this.departmentRef(body.department_id);
    if (body.title !== undefined) person.title = body.title;
    if (body.email !== undefined) person.email = body.email;
    if (body.employee_no !== undefined) person.employee_no = body.employee_no;
    if (body.notes !== undefined) person.notes = body.notes;
    // Deactivating keeps the person's current devices assigned (a separate step).
    if (body.active !== undefined) person.active = body.active;
    person.updated_at = isoNow();
    for (const item of this.assignments)
      if (item.person?.id === id) item.person = { id, name: person.name };
    this.refreshAll();
    return [200, this.personOut(person)];
  }

  // -- departments and VLANs --------------------------------------------------------------------

  private departmentOut(id: number): DepartmentOut | null {
    const department = this.departments.get(id);
    if (!department) return null;
    return {
      ...copy(department),
      person_count: [...this.persons.values()].filter((person) => person.department?.id === id)
        .length,
      vlan_ids: this.vlans
        .filter((vlan) => vlan.department?.id === id && vlan.vlan_id !== null)
        .map((vlan) => vlan.vlan_id as number),
    };
  }

  private listDepartments(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.read', false);
    if (Array.isArray(account)) return account;
    const items = [...this.departments.keys()]
      .map((id) => this.departmentOut(id))
      .filter((item): item is DepartmentOut => item !== null)
      .sort((a, b) => a.name.localeCompare(b.name, 'tr'));
    return [200, items];
  }

  private departmentExists(name: string, except: number | null): Answer | null {
    const exists = [...this.departments.values()].some(
      (item) => fold(item.name) === fold(name) && item.id !== except,
    );
    if (!exists) return null;
    const refusal = DEVICES.errors.department_exists;
    if (!refusal) throw new Error('missing error sample');
    const body = copy(refusal.body);
    const shown = `(${name.toLocaleUpperCase('tr')})`;
    body.error.message_tr = body.error.message_tr.replace(/\([^)]*\)/, shown);
    body.error.message_en = body.error.message_en.replace(/\([^)]*\)/, shown);
    return [refusal.status, body];
  }

  private createDepartment(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.manage', false);
    if (Array.isArray(account)) return account;
    const body = call.body as DepartmentCreate;
    if (!body.name.trim()) return host.error(422, 'validation_error');
    const exists = this.departmentExists(body.name.trim(), null);
    if (exists) return exists;
    const id = this.nextDepartment++;
    this.departments.set(id, { id, name: body.name.trim(), notes: body.notes ?? null });
    return [201, this.departmentOut(id)];
  }

  private updateDepartment(call: RecordedCall, host: DevicesHost, id: number): Answer {
    const account = this.allowed(call, host, 'devices.manage', false);
    if (Array.isArray(account)) return account;
    const department = this.departments.get(id);
    if (!department) return host.error(404, 'not_found');
    const body = call.body as DepartmentUpdate;
    if (body.name !== undefined && body.name !== null) {
      const exists = this.departmentExists(body.name.trim(), id);
      if (exists) return exists;
      department.name = body.name.trim();
    }
    if (body.notes !== undefined) department.notes = body.notes;
    this.refreshAll();
    return [200, this.departmentOut(id)];
  }

  private listVlans(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.read', false);
    if (Array.isArray(account)) return account;
    return [200, copy(this.vlans)];
  }

  /** A captured 409 of `routers/vlans.py` with this request's values in its texts. */
  private vlanTaken(
    code: 'vlan_exists' | 'vlan_id_taken' | 'dhcp_server_taken',
    values: { subnet?: string; vlanId?: number; server?: string; holder?: string },
  ): Answer {
    const refusal = DEVICES.errors[code];
    if (!refusal) throw new Error(`missing error sample ${code}`);
    const body = copy(refusal.body);
    // The captured texts as templates: 172.20.70.0/24 exists; VLAN 43 and dhcp43 belong to
    // (172.20.43.0/24).
    const template = (text: string) =>
      code === 'vlan_exists'
        ? text.replace('172.20.70.0/24', '{subnet}')
        : text
            .replace('(172.20.43.0/24)', '({holder})')
            .replace('dhcp43', '{server}')
            .replace(/\b34\b/, '{vlanId}');
    const params = {
      subnet: values.subnet ?? '',
      holder: values.holder ?? '',
      server: values.server ?? '',
      vlanId: String(values.vlanId ?? ''),
    };
    body.error.message_tr = fill(template(body.error.message_tr), params);
    body.error.message_en = fill(template(body.error.message_en), params);
    return [refusal.status, body];
  }

  /** 409 when another VLAN has the subnet, VLAN id or DHCP server of `changes`. */
  private vlanConflict(
    changes: { subnet?: string; vlan_id?: number | null; dhcp_server?: string | null },
    except: VlanOut | null,
  ): Answer | null {
    const others = this.vlans.filter((item) => item !== except);
    if (changes.subnet !== undefined && others.some((item) => item.subnet === changes.subnet))
      return this.vlanTaken('vlan_exists', { subnet: changes.subnet });
    const id = others.find((item) => changes.vlan_id != null && item.vlan_id === changes.vlan_id);
    if (id) return this.vlanTaken('vlan_id_taken', { vlanId: id.vlan_id ?? 0, holder: id.subnet });
    const server = others.find(
      (item) => changes.dhcp_server != null && item.dhcp_server === changes.dhcp_server,
    );
    if (server)
      return this.vlanTaken('dhcp_server_taken', {
        server: server.dhcp_server ?? '',
        holder: server.subnet,
      });
    return null;
  }

  private sortVlans(): void {
    this.vlans.sort(
      (a, b) =>
        (a.vlan_id ?? Number.MAX_SAFE_INTEGER) - (b.vlan_id ?? Number.MAX_SAFE_INTEGER) ||
        a.subnet.localeCompare(b.subnet),
    );
  }

  private createVlan(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.manage', false);
    if (Array.isArray(account)) return account;
    const body = call.body as VlanCreate;
    const subnet = networkOf(body.subnet);
    const vlanId = body.vlan_id ?? null;
    if (
      subnet === null ||
      !body.name_tr.trim() ||
      (vlanId !== null && (!Number.isInteger(vlanId) || vlanId < 1 || vlanId > 4094)) ||
      (body.dhcp_server != null && !/^[^\s:]+$/.test(body.dhcp_server.trim()))
    )
      return host.error(422, 'validation_error');
    if (body.department_id != null && !this.departments.has(body.department_id))
      return host.error(422, 'validation_error');
    const server = body.dhcp_server?.trim() || null;
    const conflict = this.vlanConflict({ subnet, vlan_id: vlanId, dhcp_server: server }, null);
    if (conflict) return conflict;
    const vlan: VlanOut = {
      subnet,
      vlan_id: vlanId,
      name_tr: body.name_tr.trim(),
      purpose: body.purpose?.trim() || null,
      dhcp_server: server,
      department: this.departmentRef(body.department_id),
      guest: body.guest ?? false,
      notes: body.notes?.trim() || null,
    };
    this.vlans.push(vlan);
    this.sortVlans();
    this.refreshAll();
    return [201, copy(vlan)];
  }

  /** Only the fields sent change; `subnet` names the VLAN, `new_subnet` moves it. */
  private updateVlan(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.manage', false);
    if (Array.isArray(account)) return account;
    const body = call.body as VlanUpdate;
    const vlan = this.vlans.find((item) => item.subnet === networkOf(body.subnet));
    if (!vlan) return host.error(404, 'not_found');
    if (
      ('name_tr' in body && !body.name_tr?.trim()) ||
      ('guest' in body && body.guest === null) ||
      ('new_subnet' in body && (body.new_subnet == null || networkOf(body.new_subnet) === null)) ||
      ('vlan_id' in body &&
        body.vlan_id != null &&
        (!Number.isInteger(body.vlan_id) || body.vlan_id < 1 || body.vlan_id > 4094)) ||
      (body.dhcp_server != null && !/^[^\s:]+$/.test(body.dhcp_server.trim()))
    )
      return host.error(422, 'validation_error');
    if (body.department_id != null && !this.departments.has(body.department_id))
      return host.error(422, 'validation_error');
    const subnet = body.new_subnet != null ? networkOf(body.new_subnet) : null;
    const server = 'dhcp_server' in body ? body.dhcp_server?.trim() || null : undefined;
    const conflict = this.vlanConflict(
      {
        ...(subnet !== null && subnet !== vlan.subnet ? { subnet } : {}),
        ...('vlan_id' in body && body.vlan_id !== vlan.vlan_id ? { vlan_id: body.vlan_id } : {}),
        ...(server !== undefined && server !== vlan.dhcp_server ? { dhcp_server: server } : {}),
      },
      vlan,
    );
    if (conflict) return conflict;
    if (subnet !== null) vlan.subnet = subnet;
    if ('vlan_id' in body) vlan.vlan_id = body.vlan_id ?? null;
    if (body.name_tr) vlan.name_tr = body.name_tr.trim();
    if ('purpose' in body) vlan.purpose = body.purpose?.trim() || null;
    if (server !== undefined) vlan.dhcp_server = server;
    if ('department_id' in body) vlan.department = this.departmentRef(body.department_id);
    if (typeof body.guest === 'boolean') vlan.guest = body.guest;
    if ('notes' in body) vlan.notes = body.notes?.trim() || null;
    this.sortVlans();
    this.refreshAll();
    return [200, copy(vlan)];
  }

  private deleteVlan(call: RecordedCall, host: DevicesHost): Answer {
    const account = this.allowed(call, host, 'devices.manage', false);
    if (Array.isArray(account)) return account;
    const subnet = networkOf(call.query.get('subnet') ?? '');
    if (subnet === null) return host.error(422, 'validation_error');
    const index = this.vlans.findIndex((item) => item.subnet === subnet);
    if (index < 0) return host.error(404, 'not_found');
    this.vlans.splice(index, 1);
    this.refreshAll();
    return [204, null];
  }

  // -- CSV import -------------------------------------------------------------------------------

  private template(call: RecordedCall, host: DevicesHost, kind: 'persons' | 'assignments'): Answer {
    return this.csv(
      call,
      host,
      kind === 'persons' ? DEVICES.template_persons : DEVICES.template_assignments,
      false,
    );
  }

  private importCsv(
    call: RecordedCall,
    host: DevicesHost,
    kind: 'persons' | 'assignments',
  ): Answer {
    const account = this.allowed(call, host, 'devices.manage');
    if (Array.isArray(account)) return account;
    const dryRun = call.query.get('dry_run') !== 'false';
    const file = multipartFile(call);
    if (!file) return sample(DEVICES.errors.upload_invalid);
    const [header = [], ...lines] = parseCsv(file.text);
    const columns = header.map((name) => name.trim().toLowerCase());
    const expected = kind === 'persons' ? PERSON_COLUMNS : ASSIGNMENT_COLUMNS;
    const ignored = kind === 'persons' ? PERSON_EXPORT_ONLY : [];
    const required = kind === 'persons' ? ['name'] : ['mac'];
    const missing = required.filter((name) => !columns.includes(name));
    const unknown = columns.filter(
      (name) => name && !expected.includes(name) && !ignored.includes(name),
    );
    if (missing.length > 0 || unknown.length > 0) {
      const refusal = DEVICES.errors.csv_header;
      if (!refusal) throw new Error('missing error sample');
      const body = copy(refusal.body);
      const detail = missing.length
        ? `missing column ${missing.join(', ')}`
        : `unknown column ${unknown.join(', ')}`;
      for (const key of ['message_tr', 'message_en'] as const)
        body.error[key] = body.error[key]
          .replace('missing column name', detail)
          .replace(PERSON_COLUMNS.join(', '), expected.join(', '));
      return [refusal.status, body];
    }
    const get = (cells: string[], name: string) => (cells[columns.indexOf(name)] ?? '').trim();
    const has = (name: string) => columns.includes(name);
    // Empty lines are skipped (Excel leaves some at the end); line numbers stay the file's.
    const numbered = lines
      .map((cells, index) => ({ line: index + 2, cells }))
      .filter((item) => item.cells.some((cell) => cell.trim() !== ''));
    if (numbered.length === 0) return error(422, 'csv_empty', DEVICE.csv_empty);
    const result =
      kind === 'persons'
        ? this.importPersons(numbered, get, has, !dryRun)
        : this.importAssignments(numbered, get, !dryRun, account.username);
    return [200, { ...result, kind, dry_run: dryRun }];
  }

  private importPersons(
    lines: { line: number; cells: string[] }[],
    get: (cells: string[], name: string) => string,
    has: (name: string) => boolean,
    apply: boolean,
  ): Omit<ImportResult, 'kind' | 'dry_run'> {
    const errors: ImportLineError[] = [];
    const preview: Record<string, unknown>[] = [];
    const steps: (() => void)[] = [];
    let created = 0;
    let updated = 0;
    let unchanged = 0;
    for (const { line, cells } of lines) {
      const name = get(cells, 'name');
      if (!name) {
        errors.push(lineError(line, 'required', { column: 'name' }));
        continue;
      }
      const departmentName = get(cells, 'department');
      const department = departmentName
        ? [...this.departments.values()].find((item) => fold(item.name) === fold(departmentName))
        : undefined;
      if (departmentName && !department) {
        errors.push(lineError(line, 'department', { value: departmentName }));
        continue;
      }
      const email = get(cells, 'email');
      if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
        errors.push(lineError(line, 'email', { value: email }));
        continue;
      }
      const number = get(cells, 'employee_no') || null;
      const numbered = number
        ? [...this.persons.values()].find((person) => person.employee_no === number)
        : undefined;
      // The export's `id` names the person (also one without an employee number).
      const id = has('id') ? get(cells, 'id') : '';
      const byId = /^[0-9]{1,18}$/.test(id) ? this.persons.get(Number(id)) : undefined;
      if (id && !byId) {
        errors.push(lineError(line, 'person_unknown_id', { value: id.slice(0, 40) }));
        continue;
      }
      if (number && byId && numbered && numbered.id !== byId.id) {
        const ids = { other: String(numbered.id), value: String(byId.id) };
        errors.push(lineError(line, 'id_mismatch', { number, ...ids }));
        continue;
      }
      const existing = byId ?? numbered;
      // Only the columns in the file; an empty cell clears the field, an empty `active` keeps it.
      const values: Record<string, string | number | boolean | null> = { name };
      if (has('department')) values.department_id = department?.id ?? null;
      for (const column of ['title', 'email', 'employee_no', 'notes'] as const)
        if (has(column)) values[column] = get(cells, column) || null;
      const active = fold(get(cells, 'active'));
      if (active)
        values.active = !['hayir', 'false', '0', 'h', 'no', 'n', 'pasif'].includes(active);
      else if (!existing) values.active = true;
      const current: Record<string, unknown> = existing
        ? { ...existing, department_id: existing.department?.id ?? null }
        : {};
      const changes = Object.keys(values)
        .filter((field) => !existing || current[field] !== values[field])
        .sort();
      if (existing && changes.length === 0) {
        unchanged += 1;
        continue;
      }
      if (existing) updated += 1;
      else created += 1;
      preview.push({
        line,
        action: existing ? 'update' : 'create',
        person_id: existing?.id ?? null,
        name,
        employee_no: number,
        changes,
        duplicate_name: [...this.persons.values()].some(
          (person) => fold(person.name) === fold(name) && person.id !== existing?.id,
        ),
      });
      steps.push(() => {
        const now = isoNow();
        const record: PersonRecord = existing ?? {
          id: this.nextPerson++,
          name,
          department: null,
          title: null,
          email: null,
          employee_no: null,
          active: true,
          notes: null,
          created_at: now,
          updated_at: now,
        };
        for (const [field, value] of Object.entries(values)) {
          if (field === 'department_id') record.department = this.departmentRef(value as number);
          else Object.assign(record, { [field]: value });
        }
        record.updated_at = now;
        this.persons.set(record.id, record);
      });
    }
    if (apply && errors.length === 0) {
      for (const step of steps) step();
      this.refreshAll();
    }
    return {
      rows: lines.length,
      created,
      updated,
      unchanged,
      errors,
      preview: preview.slice(0, PREVIEW_ROWS),
    };
  }

  private importAssignments(
    lines: { line: number; cells: string[] }[],
    get: (cells: string[], name: string) => string,
    apply: boolean,
    username: string,
  ): Omit<ImportResult, 'kind' | 'dry_run'> {
    const errors: ImportLineError[] = [];
    const preview: Record<string, unknown>[] = [];
    const steps: (() => void)[] = [];
    let created = 0;
    let updated = 0;
    let unchanged = 0;
    for (const { line, cells } of lines) {
      const mac = get(cells, 'mac').toLowerCase();
      const device = this.devices.get(mac);
      if (!device) {
        errors.push(lineError(line, 'device_unknown', { value: mac }));
        continue;
      }
      const number = get(cells, 'employee_no');
      const name = get(cells, 'person_name');
      if (!number && !name) {
        errors.push(lineError(line, 'person_missing'));
        continue;
      }
      const byName = [...this.persons.values()].filter((person) => person.name === name);
      const person = number
        ? [...this.persons.values()].find((item) => item.employee_no === number)
        : byName.length === 1
          ? byName[0]
          : undefined;
      if (!person) {
        errors.push(
          number
            ? lineError(line, 'person_unknown_no', { value: number })
            : lineError(line, 'person_unknown_name', { value: name }),
        );
        continue;
      }
      if (!person.active) {
        errors.push(lineError(line, 'person_inactive', { value: person.name }));
        continue;
      }
      const given = get(cells, 'valid_from');
      const start = given ? parseValidFrom(given) : DEVICES_NOW;
      if (start === null) {
        errors.push(lineError(line, 'valid_from', { value: given }));
        continue;
      }
      const previous = device.person;
      if (previous?.id === person.id) {
        unchanged += 1;
        continue;
      }
      if (start > DEVICES_NOW + FUTURE_SLACK_MS) {
        errors.push(
          lineError(line, 'in_future', { value: siteText(new Date(start).toISOString()) }),
        );
        continue;
      }
      const begin = Math.min(start, DEVICES_NOW); // within the slack: now
      const limit = this.earliest(mac);
      if (limit !== null && begin < limit) {
        errors.push(
          lineError(line, 'overlap', {
            value: siteText(new Date(start).toISOString()),
            mac,
            limit: siteText(new Date(limit).toISOString()),
          }),
        );
        continue;
      }
      const validFrom = new Date(begin).toISOString().replace('.000Z', 'Z');
      if (previous) updated += 1;
      else created += 1;
      preview.push({
        line,
        action: previous ? 'reassign' : 'assign',
        mac,
        person_id: person.id,
        person_name: person.name,
        previous_person_id: previous?.id ?? null,
        previous_person_name: previous?.name ?? null,
        valid_from: validFrom,
      });
      steps.push(() => {
        const current = this.assignments.find((item) => item.mac === mac && item.valid_to === null);
        if (current) current.valid_to = validFrom;
        this.assignments.unshift({
          id: this.nextAssignment++,
          mac,
          person: { id: person.id, name: person.name },
          valid_from: validFrom,
          valid_to: null,
          assigned_by: username,
          note: get(cells, 'note') || null,
          backfilled: false,
        });
        device.person = { id: person.id, name: person.name };
        device.assigned_since = validFrom;
      });
    }
    if (apply && errors.length === 0) {
      for (const step of steps) step();
      this.refreshAll();
    }
    return {
      rows: lines.length,
      created,
      updated,
      unchanged,
      errors,
      preview: preview.slice(0, PREVIEW_ROWS),
    };
  }
}
