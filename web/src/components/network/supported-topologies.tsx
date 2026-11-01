import { ChevronDown, ChevronRight, Network } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

/** The supported set-ups, in the order of KURULUM/INSTALL §0.1 (contract 2.13, 2.15). */
const ITEMS = ['site', 'leases', 'statics', 'otherDhcp', 'lines', 'doubleNat', 'unsupported'];

/**
 * "Desteklenen ağ yapıları": the docs' statement of what the 5651 chain covers, in short (the
 * same cases in the same order as the installation guide's section 0.1, nothing it does not
 * say). Collapsed until opened; on Kaynaklar and Ayarlar → Kaynaklar.
 */
export function SupportedTopologies({ className }: { className?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <section className={className} data-testid="supported-topologies">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        className="flex items-center gap-1.5 rounded-md text-13 font-medium text-accent-ink hover:underline"
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        {open ? (
          <ChevronDown aria-hidden="true" className="size-4" />
        ) : (
          <ChevronRight aria-hidden="true" className="size-4" />
        )}
        <Network aria-hidden="true" className="size-4" />
        {t('network.topology.title')}
      </button>
      <div
        id={id}
        hidden={!open}
        className="mt-2 flex flex-col gap-2 pl-6 text-13 text-muted-strong"
      >
        <ol className="flex list-decimal flex-col gap-1 pl-4">
          {ITEMS.map((item) => (
            <li key={item}>{t(`network.topology.items.${item}`)}</li>
          ))}
        </ol>
        <p className="text-xs text-muted">{t('network.topology.details')}</p>
      </div>
    </section>
  );
}
