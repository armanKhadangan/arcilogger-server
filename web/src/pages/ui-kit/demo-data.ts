/**
 * Deterministic demo data for the UI kit (same output on every load, so screenshots are stable).
 * Every value is made up: private `10.0.x.x` addresses, documentation addresses (RFC 5737) and
 * `.example` host names (RFC 2606); nothing here is real traffic or a real network.
 */
import type { SealDayStatus } from '@/components/ui/seal-calendar';

/** Small seeded PRNG (mulberry32). */
export function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick<T>(random: () => number, items: readonly T[]): T {
  const item = items[Math.floor(random() * items.length)];
  if (item === undefined) throw new Error('empty list');
  return item;
}

export interface TrafficRow {
  id: string;
  time: string;
  source: string;
  srcip: string;
  vlan: string;
  dst: string;
  action: 'accept' | 'deny' | 'blocked' | 'client-rst';
  app: string;
  hostname: string;
  bytes: number;
  raw: string;
}

/** The demo's network: senders, the ARCILOGGER server and the firewall's WAN address. */
export const DEMO_NET = {
  firewall: '10.0.0.1',
  router: '10.0.0.2',
  switches: ['10.0.0.11', '10.0.0.12'],
  arcilogger: '10.0.0.20',
  wan: '192.0.2.10',
} as const;

/** VLAN `n` is `10.0.n.0/24`. */
const VLANS = [
  { id: 10, name: 'Ağ Cihazları' },
  { id: 20, name: 'Sunucular' },
  { id: 41, name: 'Finans' },
  { id: 42, name: 'Planlama' },
  { id: 43, name: 'İdari İşler' },
  { id: 60, name: 'Wi-Fi Personel' },
] as const;

const DESTINATIONS = [
  { ip: '203.0.113.21', port: 443, app: 'Example.Mail', host: 'mail.example.com' },
  { ip: '203.0.113.22', port: 443, app: 'Example.Docs', host: 'docs.example.com' },
  { ip: '203.0.113.23', port: 443, app: 'Example.Office', host: 'office.example.net' },
  { ip: '198.51.100.24', port: 443, app: 'Example.Video', host: 'video.example.org' },
  { ip: '198.51.100.25', port: 443, app: 'Example.Chat', host: 'chat.example.org' },
  { ip: '203.0.113.26', port: 443, app: 'SSL_TLSv1.3', host: 'cdn.example.net' },
  { ip: '198.51.100.27', port: 443, app: 'Example.Social', host: 'social.example.com' },
  { ip: '198.51.100.53', port: 53, app: 'DNS', host: '' },
] as const;

const ACTIONS: readonly TrafficRow['action'][] = [
  'accept',
  'accept',
  'accept',
  'accept',
  'client-rst',
  'deny',
  'blocked',
];

/** `count` FortiGate-like traffic rows on 12.10.2026, newest first. */
export function trafficRows(count: number): TrafficRow[] {
  const random = seeded(5651);
  const start = Date.UTC(2026, 9, 12, 5, 0, 0); // 08:00 Istanbul
  const rows: TrafficRow[] = [];
  for (let i = 0; i < count; i += 1) {
    const vlan = pick(random, VLANS);
    const dest = pick(random, DESTINATIONS);
    const host = 20 + Math.floor(random() * 200);
    const srcip = `10.0.${vlan.id}.${host}`;
    const srcport = 49152 + Math.floor(random() * 16000);
    const time = new Date(
      start + Math.floor((i / count) * 10 * 3600 * 1000) + Math.floor(random() * 1000),
    );
    const action = pick(random, ACTIONS);
    const bytes = Math.floor(random() ** 3 * 50_000_000);
    const local = new Date(time.getTime() + 3 * 3600 * 1000).toISOString();
    rows.push({
      id: `r${i}`,
      time: time.toISOString(),
      source: 'fortigate',
      srcip,
      vlan: `${vlan.id} · ${vlan.name}`,
      dst: `${dest.ip}:${dest.port}`,
      action,
      app: dest.app,
      hostname: dest.host,
      bytes,
      raw:
        `<189>date=${local.slice(0, 10)} time=${local.slice(11, 19)} devname="FIREWALL-01" ` +
        `logid="0000000013" type="traffic" subtype="forward" level="notice" vd="root" ` +
        `srcip=${srcip} srcport=${srcport} srcintf="port2" dstip=${dest.ip} dstport=${dest.port} ` +
        `dstintf="wan1" action="${action}" policyid=12 policyname="internet-out" ` +
        `trandisp="snat" transip=${DEMO_NET.wan} transport=${srcport} app="${dest.app}" ` +
        `sentbyte=${Math.floor(bytes / 9)} rcvdbyte=${bytes - Math.floor(bytes / 9)}`,
    });
  }
  return rows.reverse();
}

/** Hourly download/upload bytes for one day. */
export function hourlyTraffic(): { hour: string; download: number; upload: number }[] {
  const random = seeded(12);
  return Array.from({ length: 24 }, (_, hour) => {
    const work = hour >= 8 && hour < 18 ? (hour === 12 ? 0.6 : 1) : 0.08;
    const download = Math.floor((2.2e9 + random() * 1.4e9) * work);
    return {
      hour: String(hour).padStart(2, '0'),
      download,
      upload: Math.floor(download * (0.12 + random() * 0.08)),
    };
  });
}

export const CATEGORY_SPLIT = [
  { label: 'İş uygulamaları', value: 412 * 60 },
  { label: 'Arama ve bilgi', value: 238 * 60 },
  { label: 'Video', value: 171 * 60 },
  { label: 'Sosyal medya', value: 96 * 60 },
  { label: 'Haber', value: 54 * 60 },
  { label: 'Alışveriş', value: 31 * 60 },
  { label: 'Diğer', value: 48 * 60, other: true },
];

/** 14 days × 24 hours of active minutes for one person. */
export function heatmapRows(): { key: string; label: string; values: number[] }[] {
  const random = seeded(171);
  const days = [
    '29.09',
    '30.09',
    '01.10',
    '02.10',
    '03.10',
    '04.10',
    '05.10',
    '06.10',
    '07.10',
    '08.10',
    '09.10',
    '10.10',
    '11.10',
    '12.10',
  ];
  return days.map((day, index) => {
    const weekend = index % 7 === 4 || index % 7 === 5;
    return {
      key: day,
      label: day,
      values: Array.from({ length: 24 }, (_, hour) => {
        if (weekend) return random() < 0.08 ? Math.floor(random() * 20) : 0;
        if (hour < 8 || hour > 18) return random() < 0.05 ? Math.floor(random() * 15) : 0;
        if (hour === 12) return Math.floor(random() * 25);
        return Math.floor(20 + random() * 40);
      }),
    };
  });
}

/** Sealing status of October 2026 up to the 12th. */
export function sealDays(): Record<string, SealDayStatus> {
  const days: Record<string, SealDayStatus> = {};
  for (let day = 1; day <= 11; day += 1) {
    days[`2026-10-${String(day).padStart(2, '0')}`] = 'sealed';
  }
  days['2026-10-04'] = 'late';
  days['2026-10-07'] = 'failed';
  days['2026-10-12'] = 'pending';
  return days;
}

export const MESSAGES_PER_MINUTE = [
  880, 940, 1010, 1190, 1260, 1150, 1230, 1310, 1240, 1200, 1270, 1284,
];
export const CREDIT_HISTORY = [
  9999, 9970, 9939, 9909, 9878, 9848, 9817, 9787, 9756, 9726, 9695, 9634,
];
export const CLOCK_OFFSET_MS = [4.1, 3.6, 2.9, 3.3, 3.8, 2.7, 3.1, 3.5, 2.8, 3.0, 3.4, 3.2];
