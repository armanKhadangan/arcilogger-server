import {
  Archive,
  CircleCheck,
  Clock,
  Coins,
  FileArchive,
  FileSearch,
  FolderInput,
  Hash,
  Lock,
  Moon,
  Plus,
  RadioTower,
  Search,
  ServerCog,
  Stamp,
  Sun,
  Timer,
  Trash2,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { StackedAreaChart } from '@/components/charts/area-chart';
import { ChartFrame } from '@/components/charts/chart-frame';
import { DonutChart } from '@/components/charts/donut-chart';
import { sliceColor } from '@/components/charts/palette';
import { PageHeader } from '@/components/layout/page-header';
import {
  Badge,
  SeverityBadge,
  StatusBadge,
  type Severity,
  type Status,
} from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  KpiCard,
} from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { CodeBlock } from '@/components/ui/code-block';
import { Combobox } from '@/components/ui/combobox';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import { DateRangePicker, presetRange, type Range } from '@/components/ui/date-range-picker';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty-state';
import { Heatmap } from '@/components/ui/heatmap';
import { Input } from '@/components/ui/input';
import { Field, Label } from '@/components/ui/label';
import { SealCalendar } from '@/components/ui/seal-calendar';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Stepper } from '@/components/ui/stepper';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DayActivityBar, Timeline } from '@/components/ui/timeline';
import { toast } from '@/components/ui/toast';
import { SimpleTooltip } from '@/components/ui/tooltip';
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatTime,
} from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

import {
  CATEGORY_SPLIT,
  CLOCK_OFFSET_MS,
  CREDIT_HISTORY,
  DEMO_NET,
  MESSAGES_PER_MINUTE,
  heatmapRows,
  hourlyTraffic,
  sealDays,
  trafficRows,
  type TrafficRow,
} from './demo-data';

const SECTIONS = [
  'colors',
  'typography',
  'buttons',
  'inputs',
  'badges',
  'cards',
  'charts',
  'table',
  'tabs',
  'overlays',
  'feedback',
  'timeline',
  'heatmap',
  'calendar',
  'stepper',
  'code',
] as const;

type SectionKey = (typeof SECTIONS)[number];

function Section({ id, children }: { id: SectionKey; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className="scroll-mt-20 border-t border-border py-8"
    >
      <h2 id={`${id}-title`} className="mb-4 text-xl font-semibold">
        {t(`uiKit.sections.${id}`)}
      </h2>
      {children}
    </section>
  );
}

function Swatch({ name, cssVar }: { name: string; cssVar: string }) {
  return (
    <li className="flex items-center gap-2">
      <span
        aria-hidden="true"
        className="size-9 shrink-0 rounded-lg border border-border"
        style={{ background: `var(${cssVar})` }}
      />
      <span className="flex flex-col">
        <span className="text-13 font-medium">{name}</span>
        <code className="text-xs text-muted">{cssVar}</code>
      </span>
    </li>
  );
}

const SWATCH_GROUPS: { title: string; items: [string, string][] }[] = [
  {
    title: 'Yüzeyler / Surfaces',
    items: [
      ['Arka plan', '--background'],
      ['Yüzey', '--surface'],
      ['Hafif ton', '--subtle'],
      ['Kenarlık', '--border'],
      ['Metin', '--text'],
      ['İkincil metin', '--muted'],
    ],
  },
  {
    title: 'Marka / Brand',
    items: [
      ['Birincil', '--primary'],
      ['Birincil üzeri', '--primary-hover'],
      ['Birincil yumuşak', '--primary-soft'],
      ['Vurgu', '--accent'],
      ['Vurgu yumuşak', '--accent-soft'],
      ['Odak halkası', '--focus'],
    ],
  },
  {
    title: 'Durum / Status',
    items: [
      ['Başarılı', '--success'],
      ['Uyarı', '--warning'],
      ['Tehlike', '--danger'],
      ['Bilgi', '--info'],
      ['Başarılı yumuşak', '--success-soft'],
      ['Tehlike yumuşak', '--danger-soft'],
    ],
  },
  {
    title: 'Grafik / Chart',
    items: [
      ['Seri 1', '--chart-1'],
      ['Seri 2', '--chart-2'],
      ['Seri 3', '--chart-3'],
      ['Seri 4', '--chart-4'],
      ['Seri 5', '--chart-5'],
      ['Seri 6', '--chart-6'],
      ['Seri 7', '--chart-7'],
      ['Diğer', '--chart-other'],
    ],
  },
  {
    title: 'Isı ölçeği / Heat ramp',
    items: [
      ['0', '--heat-0'],
      ['1', '--heat-1'],
      ['2', '--heat-2'],
      ['3', '--heat-3'],
      ['4', '--heat-4'],
      ['5', '--heat-5'],
    ],
  },
];

const PERSONS = [
  { value: 'p1', label: 'Tolga Yılmaz', hint: 'İdari İşler' },
  { value: 'p2', label: 'Ayşe Yılmaz', hint: 'Finans' },
  { value: 'p3', label: 'Mehmet Öztürk', hint: 'Planlama' },
  { value: 'p4', label: 'Şule Çağlar', hint: 'Finans' },
  { value: 'p5', label: 'İsmail Güneş', hint: 'Planlama' },
];

const STATUSES: Status[] = [
  'sealed',
  'pending',
  'late',
  'failed',
  'noData',
  'active',
  'silent',
  'unknown',
];
const SEVERITIES: Severity[] = ['info', 'low', 'medium', 'high', 'critical'];

const WIZARD_STEPS = [
  { id: 'admin', title: 'Yönetici', description: 'Kullanıcı ve 2FA' },
  { id: 'site', title: 'Site', description: 'Mesai saatleri' },
  { id: 'sources', title: 'Kaynaklar', description: 'FortiGate, MikroTik' },
  { id: 'kamusm', title: 'Kamu SM', description: 'Önce test sunucusu' },
  { id: 'replica', title: 'Replika', description: 'NAS paylaşımı' },
  { id: 'summary', title: 'Özet' },
];

const FORTIGATE_SNIPPET = `config log syslogd setting
    set status enable
    set server "${DEMO_NET.arcilogger}"
    set mode udp
    set port 514
    set facility local7
    set format default
    set source-ip ${DEMO_NET.firewall}
end`;

const RAW_LINE = `2026-10-12T10:24:31.483350+03:00 ${DEMO_NET.firewall} <189>date=2026-10-12 time=10:24:31 devname="FIREWALL-01" devid="FGT0000000000000" eventtime=1791789871482913517 tz="+0300" logid="0000000013" type="traffic" subtype="forward" level="notice" vd="root" srcip=10.0.43.25 srcport=53124 srcintf="port2" dstip=203.0.113.21 dstport=443 dstintf="wan1" action="accept" policyid=12 trandisp="snat" transip=${DEMO_NET.wan} transport=53124 app="Example.Mail"`;

function trafficColumns(): AnyColumnDef<TrafficRow>[] {
  const lang = currentLang();
  return [
    {
      id: 'time',
      accessorFn: (row) => row.time,
      header: 'Zaman',
      size: 170,
      cell: (info) => formatDateTime(info.getValue<string>()),
      meta: { label: 'Zaman', mono: true, exportValue: (row) => formatDateTime(row.time) },
    },
    {
      id: 'srcip',
      accessorFn: (row) => row.srcip,
      header: 'Kaynak IP',
      size: 150,
      meta: { label: 'Kaynak IP', mono: true },
    },
    {
      id: 'vlan',
      accessorFn: (row) => row.vlan,
      header: 'VLAN',
      size: 190,
      meta: { label: 'VLAN' },
    },
    {
      id: 'dst',
      accessorFn: (row) => row.dst,
      header: 'Hedef',
      size: 190,
      meta: { label: 'Hedef', mono: true },
    },
    {
      id: 'app',
      accessorFn: (row) => row.app,
      header: 'Uygulama',
      size: 180,
      meta: { label: 'Uygulama' },
    },
    {
      id: 'action',
      accessorFn: (row) => row.action,
      header: 'Eylem',
      size: 120,
      meta: { label: 'Eylem' },
      cell: (info) => {
        const action = info.getValue<TrafficRow['action']>();
        const tone =
          action === 'accept' ? 'success' : action === 'client-rst' ? 'neutral' : 'danger';
        return <Badge tone={tone}>{action}</Badge>;
      },
    },
    {
      id: 'bytes',
      accessorFn: (row) => row.bytes,
      header: 'Veri',
      size: 110,
      cell: (info) => formatBytes(info.getValue<number>(), lang),
      meta: { label: 'Veri (bayt)', numeric: true },
    },
  ];
}

export function UiKitPage() {
  const { t } = useTranslation();
  const lang = currentLang();
  const { resolved, setPreference } = useTheme();
  const [person, setPerson] = useState<string | null>('p2');
  const [range, setRange] = useState<Range>(() => presetRange('last7'));
  const [selectedRow, setSelectedRow] = useState<TrafficRow | null>(null);
  const [month, setMonth] = useState(() => new Date(2026, 9, 1));
  const [sealDay, setSealDay] = useState<string | null>('2026-10-11');
  const [step, setStep] = useState(3);
  const [checked, setChecked] = useState(true);

  const rows = useMemo(() => trafficRows(10_000), []);
  const columns = useMemo(() => trafficColumns(), []);
  const hourly = useMemo(() => hourlyTraffic(), []);
  const heat = useMemo(() => heatmapRows(), []);
  const seals = useMemo(() => sealDays(), []);
  const categoryTotal = CATEGORY_SPLIT.reduce((sum, slice) => sum + slice.value, 0);

  return (
    <div>
      <PageHeader
        title={t('uiKit.title')}
        description={t('uiKit.intro')}
        actions={
          <Button
            variant="secondary"
            onClick={() => {
              setPreference(resolved === 'dark' ? 'light' : 'dark');
            }}
            aria-label={resolved === 'dark' ? t('auth.themeLight') : t('auth.themeDark')}
            data-testid="ui-kit-theme-toggle"
          >
            {resolved === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
            {resolved === 'dark' ? t('topbar.themeLight') : t('topbar.themeDark')}
          </Button>
        }
      />

      <nav aria-label={t('uiKit.title')} className="mb-2 flex flex-wrap gap-1.5">
        {SECTIONS.map((key) => (
          <a
            key={key}
            href={`#${key}`}
            className="rounded-md border border-border bg-surface px-2.5 py-1 text-13 text-foreground hover:bg-subtle"
          >
            {t(`uiKit.sections.${key}`)}
          </a>
        ))}
      </nav>

      <Section id="colors">
        <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
          {SWATCH_GROUPS.map((group) => (
            <div key={group.title} className="flex flex-col gap-3">
              <h3 className="text-13 font-semibold text-muted">{group.title}</h3>
              <ul className="grid grid-cols-2 gap-3">
                {group.items.map(([name, cssVar]) => (
                  <Swatch key={cssVar} name={name} cssVar={cssVar} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Section>

      <Section id="typography">
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="flex flex-col gap-2">
            <p className="text-3xl font-semibold">30 — Kayıt · Mühür · Rapor</p>
            <p className="text-2xl font-semibold">24 — Sayfa başlığı</p>
            <p className="text-xl font-semibold">20 — Bölüm başlığı</p>
            <p className="text-base font-medium">16 — Kart başlığı</p>
            <p className="text-sm">14 — Gövde metni: ı İ ş Ş ğ Ğ ç Ç ö Ö ü Ü</p>
            <p className="text-13 text-muted">13 — Yardımcı metin ve tablo hücreleri</p>
            <p className="text-xs text-muted">12 — Etiketler ve eksenler</p>
          </div>
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              Ağırlıklar: <span className="font-normal">400</span> ·{' '}
              <span className="font-medium">500</span> · <span className="font-semibold">600</span>{' '}
              · <span className="font-bold">700</span>
            </p>
            <p className="text-sm tabular">
              Tablo rakamları: {formatNumber(1234567)} · {formatBytes(1_610_612_736)} ·{' '}
              {formatDuration(8100)}
            </p>
            <p className="font-mono text-13">
              10.0.43.25 · 00:00:5E:00:53:01 · sha256 3f1a9c0e…7b42
            </p>
            <p className="text-sm">{formatDateTime('2026-10-12T07:24:31Z')} (Europe/Istanbul)</p>
          </div>
        </div>
      </Section>

      <Section id="buttons">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <Button>Birincil</Button>
            <Button variant="secondary">İkincil</Button>
            <Button variant="ghost">Hayalet</Button>
            <Button variant="danger">
              <Trash2 aria-hidden="true" />
              Sil
            </Button>
            <Button variant="link">Bağlantı</Button>
            <Button disabled>Devre dışı</Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm">
              <Plus aria-hidden="true" />
              Küçük
            </Button>
            <Button size="md">
              <Stamp aria-hidden="true" />
              Şimdi mühürle
            </Button>
            <Button size="lg">
              <FileArchive aria-hidden="true" />
              Kanıt paketi oluştur
            </Button>
            <SimpleTooltip content="Ara">
              <Button size="icon" variant="secondary" aria-label="Ara">
                <Search aria-hidden="true" />
              </Button>
            </SimpleTooltip>
          </div>
        </div>
      </Section>

      <Section id="inputs">
        <div className="grid max-w-4xl gap-4 md:grid-cols-2">
          <Field id="kit-ip" label="Genel IP / WAN IP" hint={`Örn. ${DEMO_NET.wan}`} required>
            <Input
              id="kit-ip"
              mono
              placeholder={DEMO_NET.wan}
              defaultValue={DEMO_NET.wan}
              aria-describedby="kit-ip-hint"
            />
          </Field>
          <Field id="kit-port" label="Port" error="Port 1–65535 arasında olmalı.">
            <Input
              id="kit-port"
              mono
              defaultValue="70000"
              aria-invalid="true"
              aria-describedby="kit-port-error"
            />
          </Field>
          <Field id="kit-search" label="Arama">
            <Input
              id="kit-search"
              leadingIcon={<Search />}
              placeholder="srcip:10.0.41.17 action:blocked"
            />
          </Field>
          <Field id="kit-source" label="Kaynak">
            <Select defaultValue="fortigate">
              <SelectTrigger id="kit-source">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="fortigate">fortigate · {DEMO_NET.firewall}</SelectItem>
                <SelectItem value="router">router · {DEMO_NET.router}</SelectItem>
                {DEMO_NET.switches.map((address, index) => (
                  <SelectItem key={address} value={`switch-${String(index + 1)}`}>
                    switch-{index + 1} · {address}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field id="kit-person" label="Kişi">
            <Combobox
              id="kit-person"
              options={PERSONS}
              value={person}
              onChange={setPerson}
              aria-label="Kişi"
            />
          </Field>
          <Field id="kit-range" label={t('dateRange.label')}>
            <DateRangePicker
              id="kit-range"
              value={range}
              onChange={setRange}
              className="w-full justify-start"
            />
          </Field>
          <div className="flex items-center gap-2">
            <Checkbox
              id="kit-check"
              checked={checked}
              onCheckedChange={(value) => {
                setChecked(value === true);
              }}
            />
            <Label htmlFor="kit-check">Kişi bazlı raporlarda yönetimi hariç tut</Label>
          </div>
        </div>
      </Section>

      <Section id="badges">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            {(
              [
                'neutral',
                'primary',
                'accent',
                'success',
                'warning',
                'danger',
                'info',
                'outline',
              ] as const
            ).map((tone) => (
              <Badge key={tone} tone={tone}>
                {tone}
              </Badge>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            {STATUSES.map((status) => (
              <StatusBadge key={status} status={status} />
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            {SEVERITIES.map((severity) => (
              <SeverityBadge key={severity} severity={severity} />
            ))}
          </div>
        </div>
      </Section>

      <Section id="cards">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard
            label="Log alımı"
            icon={RadioTower}
            value="4/4 kaynak aktif"
            hint={`${formatNumber(1284, 0, lang)} msg/dk`}
            trend={MESSAGES_PER_MINUTE}
            trendLabel="Mesaj/dk, son 12 saat"
            delta={{ text: '+6 %', direction: 'up' }}
          />
          <KpiCard
            label="Dünkü mühür"
            icon={Stamp}
            value="12.10.2026"
            hint="00:10 · seri 4F2A…, doğrulandı"
            status={<StatusBadge status="sealed" />}
          />
          <KpiCard
            label="Kontör"
            icon={Coins}
            value={`${formatNumber(9634, 0, lang)} kaldı`}
            hint="~26 yıl yeter"
            trend={CREDIT_HISTORY}
            trendLabel="Kalan kontör"
            delta={{ text: '−365 / yıl', direction: 'down', upIsGood: true }}
          />
          <KpiCard
            label="Saat sapması"
            icon={Timer}
            value="3 ms"
            hint={`NTP ${DEMO_NET.router}`}
            trend={CLOCK_OFFSET_MS}
            trendLabel="Saat sapması (ms)"
            formatTrend={(value) => `${formatNumber(value, 1, lang)} ms`}
          />
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-0.5">
                <CardTitle>Bugünkü kullanım (şirket)</CardTitle>
                <CardDescription>Aktif kullanıcı 42 · Aktif süre 186 sa · 38,2 GB</CardDescription>
              </div>
              <Badge tone="info">{t('common.estimateShort')}</Badge>
            </CardHeader>
            <CardContent>
              <p className="text-13 text-muted">{t('common.estimate')}</p>
            </CardContent>
            <CardFooter>Son güncelleme {formatTime('2026-10-12T07:24:31Z', true)}</CardFooter>
          </Card>
          <KpiCard
            label="Aktif süre (kişi başı)"
            value={formatDuration(4 * 3600 + 25 * 60, lang)}
            estimate
            hint="Ortalama, bugün"
          />
        </div>
      </Section>

      <Section id="charts">
        <div className="grid gap-4 lg:grid-cols-3">
          <ChartFrame
            className="lg:col-span-2"
            title="İndirilen / yüklenen (saatlik)"
            description="12.10.2026 · tüm şirket"
            legend={[
              { label: 'İndirilen', color: 'var(--chart-1)' },
              { label: 'Yüklenen', color: 'var(--chart-2)' },
            ]}
            table={{
              columns: ['Saat', 'İndirilen', 'Yüklenen'],
              rows: hourly.map((row) => [
                `${row.hour}:00`,
                formatBytes(row.download, lang),
                formatBytes(row.upload, lang),
              ]),
            }}
          >
            <StackedAreaChart
              data={hourly}
              xKey="hour"
              series={[
                { key: 'download', label: 'İndirilen' },
                { key: 'upload', label: 'Yüklenen' },
              ]}
              formatValue={(value) => formatBytes(value, lang)}
              formatX={(value) => `${String(value)}:00`}
            />
          </ChartFrame>
          <ChartFrame
            title="Kategoriler"
            description="Aktif süre, tahmini"
            legend={CATEGORY_SPLIT.map((slice, index) => ({
              label: slice.label,
              color: sliceColor(slice, index),
              value: formatDuration(slice.value, lang),
            }))}
            table={{
              columns: ['Kategori', 'Süre', 'Pay'],
              rows: CATEGORY_SPLIT.map((slice) => [
                slice.label,
                formatDuration(slice.value, lang),
                `${formatNumber((slice.value / categoryTotal) * 100, 1, lang)} %`,
              ]),
            }}
          >
            <DonutChart
              data={CATEGORY_SPLIT}
              formatValue={(value) => formatDuration(value, lang)}
              centerLabel={formatDuration(categoryTotal, lang)}
              centerHint={t('common.estimateShort')}
            />
          </ChartFrame>
        </div>
      </Section>

      <Section id="table">
        <DataTable
          data={rows}
          columns={columns}
          caption="FortiGate trafik kayıtları (örnek)"
          getRowId={(row) => row.id}
          onRowClick={setSelectedRow}
          exportFileName="arcilogger-trafik"
          height={420}
        />
        <Sheet
          open={selectedRow !== null}
          onOpenChange={(open) => {
            if (!open) setSelectedRow(null);
          }}
        >
          <SheetContent>
            {selectedRow ? (
              <>
                <SheetHeader>
                  <SheetTitle className="text-base font-semibold">Kayıt ayrıntısı</SheetTitle>
                  <SheetDescription className="text-13 text-muted">
                    {formatDateTime(selectedRow.time)} · {selectedRow.source}
                  </SheetDescription>
                </SheetHeader>
                <SheetBody className="flex flex-col gap-4">
                  <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-2 text-13">
                    <dt className="text-muted">Kaynak IP</dt>
                    <dd className="font-mono">{selectedRow.srcip}</dd>
                    <dt className="text-muted">VLAN</dt>
                    <dd>{selectedRow.vlan}</dd>
                    <dt className="text-muted">Hedef</dt>
                    <dd className="font-mono">{selectedRow.dst}</dd>
                    <dt className="text-muted">Uygulama</dt>
                    <dd>{selectedRow.app}</dd>
                    <dt className="text-muted">Alan adı</dt>
                    <dd className="font-mono">{selectedRow.hostname || '—'}</dd>
                    <dt className="text-muted">Veri</dt>
                    <dd className="tabular">{formatBytes(selectedRow.bytes, lang)}</dd>
                  </dl>
                  <CodeBlock label="Ham kayıt (değiştirilmemiş)" code={selectedRow.raw} wrap />
                </SheetBody>
              </>
            ) : null}
          </SheetContent>
        </Sheet>
      </Section>

      <Section id="tabs">
        <Tabs defaultValue="company">
          <TabsList>
            <TabsTrigger value="company">Şirket</TabsTrigger>
            <TabsTrigger value="department">Departman</TabsTrigger>
            <TabsTrigger value="person">Kişi</TabsTrigger>
            <TabsTrigger value="device">Cihaz</TabsTrigger>
            <TabsTrigger value="site">Site</TabsTrigger>
            <TabsTrigger value="category">Kategori</TabsTrigger>
          </TabsList>
          <TabsContent value="company">
            <p className="text-sm text-muted">Şirket geneli kullanım özeti burada gösterilir.</p>
          </TabsContent>
          <TabsContent value="department">
            <p className="text-sm text-muted">Departman tablosu.</p>
          </TabsContent>
          <TabsContent value="person">
            <p className="text-sm text-muted">Kişi gün görünümü (yetkili roller).</p>
          </TabsContent>
          <TabsContent value="device">
            <p className="text-sm text-muted">Cihaz görünümü.</p>
          </TabsContent>
          <TabsContent value="site">
            <p className="text-sm text-muted">youtube.com'u kim, ne kadar kullandı.</p>
          </TabsContent>
          <TabsContent value="category">
            <p className="text-sm text-muted">Kategori görünümü.</p>
          </TabsContent>
        </Tabs>
      </Section>

      <Section id="overlays">
        <div className="flex flex-wrap gap-2">
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="secondary">
                <Stamp aria-hidden="true" />
                Diyalog
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>11.10.2026 gününü şimdi mühürle?</DialogTitle>
                <DialogDescription>
                  Gece çalışması başarısız oldu. Manifest oluşturulacak ve Kamu SM ile zaman damgası
                  alınacak (1 kontör).
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="secondary">{t('common.cancel')}</Button>
                </DialogClose>
                <DialogClose asChild>
                  <Button
                    onClick={() => {
                      toast({
                        title: '11.10.2026 günü mühürlendi ve doğrulandı.',
                        variant: 'success',
                      });
                    }}
                  >
                    Şimdi mühürle
                  </Button>
                </DialogClose>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Button
            variant="secondary"
            onClick={() => {
              setSelectedRow(rows[0] ?? null);
            }}
          >
            <FileSearch aria-hidden="true" />
            Yan panel
          </Button>
          <SimpleTooltip content={t('common.estimate')}>
            <Button variant="secondary">İpucu</Button>
          </SimpleTooltip>
          <Button
            variant="secondary"
            onClick={() =>
              toast({ title: '12.10.2026 günü mühürlendi ve doğrulandı.', variant: 'success' })
            }
          >
            Başarılı bildirim
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              toast({
                title: 'Mühürleme başarısız',
                description: 'Kamu SM sunucusuna ulaşılamadı. 15 dk sonra tekrar denenecek.',
                variant: 'danger',
              })
            }
          >
            Hata bildirimi
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              toast({
                title: 'Kontör azaldı',
                description: '480 kontör kaldı (eşik 500).',
                variant: 'warning',
              })
            }
          >
            Uyarı bildirimi
          </Button>
        </div>
      </Section>

      <Section id="feedback">
        <div className="grid gap-4 lg:grid-cols-2">
          <EmptyState
            icon={Search}
            title={t('common.noResults')}
            action={
              <Button variant="secondary" size="sm">
                Son 7 güne genişlet
              </Button>
            }
          />
          <Card
            className="flex flex-col gap-3 p-4"
            role="status"
            aria-busy="true"
            aria-label={t('common.loading')}
          >
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-5/6" />
            <Skeleton className="h-3 w-2/3" />
          </Card>
        </div>
      </Section>

      <Section id="timeline">
        <div className="grid gap-8 lg:grid-cols-2">
          <Timeline
            items={[
              {
                id: '1',
                time: '00:10:00',
                title: 'Günlük dosyalar kapatıldı',
                description: '4 kaynak, 1.284.551 satır',
                icon: FolderInput,
                tone: 'neutral',
              },
              {
                id: '2',
                time: '00:10:41',
                title: 'Sıkıştırıldı ve özetlendi',
                description: 'gzip -n -9 · SHA-256',
                icon: Hash,
                tone: 'neutral',
              },
              {
                id: '3',
                time: '00:10:44',
                title: 'Manifest oluşturuldu',
                description: 'Önceki gün özetiyle zincirlendi',
                icon: Archive,
                tone: 'info',
              },
              {
                id: '4',
                time: '00:10:46',
                title: 'Kamu SM zaman damgası',
                description: 'seri 4F2A…, 1 kontör',
                icon: Clock,
                tone: 'primary',
              },
              {
                id: '5',
                time: '00:10:47',
                title: 'Doğrulandı',
                description: 'İmza, sertifika zinciri, özet eşleşmesi',
                icon: CircleCheck,
                tone: 'success',
              },
              {
                id: '6',
                time: '00:10:47',
                title: 'Değiştirilemez yapıldı',
                description: 'Salt okunur + NTFS izinleri',
                icon: Lock,
                tone: 'success',
              },
              {
                id: '7',
                time: '00:11:30',
                title: 'Replika güncel',
                description: 'NAS WORM paylaşımı, özetler doğrulandı',
                icon: ServerCog,
                tone: 'success',
              },
            ]}
          />
          <DayActivityBar
            label="12.10.2026 aktif dakikalar, kategoriye göre"
            lang={lang}
            categories={['İş uygulamaları', 'Arama ve bilgi', 'Video', 'Sosyal medya']}
            segments={[
              { start: 8 * 60 + 12, end: 9 * 60 + 40, category: 'İş uygulamaları' },
              { start: 9 * 60 + 40, end: 10 * 60 + 5, category: 'Arama ve bilgi' },
              { start: 10 * 60 + 5, end: 12 * 60, category: 'İş uygulamaları' },
              { start: 12 * 60 + 10, end: 12 * 60 + 45, category: 'Video' },
              { start: 12 * 60 + 45, end: 13 * 60, category: 'Sosyal medya' },
              { start: 13 * 60 + 5, end: 15 * 60 + 30, category: 'İş uygulamaları' },
              { start: 15 * 60 + 30, end: 16 * 60, category: 'Arama ve bilgi' },
              { start: 16 * 60, end: 17 * 60 + 50, category: 'İş uygulamaları' },
            ]}
          />
        </div>
      </Section>

      <Section id="heatmap">
        <Heatmap
          rows={heat}
          max={60}
          caption="Ayşe Yılmaz · aktif dakika, saat başına, son 14 gün"
          formatValue={(minutes) => formatDuration(minutes * 60, lang)}
        />
      </Section>

      <Section id="calendar">
        <div className="flex flex-wrap items-start gap-8">
          <SealCalendar
            month={month}
            days={seals}
            selected={sealDay}
            onSelect={setSealDay}
            onMonthChange={setMonth}
            today={new Date(2026, 9, 12, 18, 0)}
          />
          {sealDay ? (
            <Card className="w-full max-w-sm">
              <CardHeader>
                <CardTitle>{sealDay.split('-').reverse().join('.')}</CardTitle>
                <StatusBadge status={seals[sealDay] ?? 'noData'} />
              </CardHeader>
              <CardContent className="flex flex-col gap-1 text-13 text-muted">
                <span>4 dosya · 1.284.551 satır · 412 MB</span>
                <span className="font-mono text-xs">manifest sha256 9b1c…e04f</span>
                <span>Belirteç: seri 4F2A…, genTime 00:10:46</span>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </Section>

      <Section id="stepper">
        <div className="flex max-w-4xl flex-col gap-6">
          <Stepper steps={WIZARD_STEPS} current={step} label="İlk kurulum sihirbazı" />
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              disabled={step === 0}
              onClick={() => {
                setStep((value) => Math.max(0, value - 1));
              }}
            >
              {t('common.back')}
            </Button>
            <Button
              disabled={step === WIZARD_STEPS.length - 1}
              onClick={() => {
                setStep((value) => Math.min(WIZARD_STEPS.length - 1, value + 1));
              }}
            >
              {t('common.next')}
            </Button>
          </div>
        </div>
      </Section>

      <Section id="code">
        <div className="grid gap-4 lg:grid-cols-2">
          <CodeBlock label="FortiGate CLI" code={FORTIGATE_SNIPPET} />
          <CodeBlock label="Ham kayıt" code={RAW_LINE} wrap />
        </div>
      </Section>
    </div>
  );
}
