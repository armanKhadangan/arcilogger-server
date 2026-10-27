import { Zap } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { currentLang } from '@/lib/i18n';
import type { FieldCatalogue } from '@/lib/search-types';

/** Example queries (contract 2.2.2), each explained in `search.help.examples.<index>`. */
export const EXAMPLES = [
  'srcip:172.20.41.105 type:web action:blocked',
  'domain:example.com -action:passthrough',
  '"login failure" source:core-r1',
  'vlan:41 dstport:80,443',
  'type:dhcp mac:00:00:5e:00:53:21',
  'error:fortigate.*',
] as const;

/** The rules of the language, in the help's order (`search.help.rules.<key>`). */
const RULES = ['and', 'or', 'not', 'quotes', 'present', 'none'] as const;
/** What makes a query fast or slow (`search.help.speed.<key>`). */
const SPEED = [
  'indexedFields',
  'srcipIndex',
  'scanUnindexed',
  'case',
  'truncated',
  'time',
] as const;

/**
 * "?" of the query bar: the language (AND by spaces, OR by commas inside one field, `-` to
 * exclude, quotes), every field of the catalogue with its other names, an example and whether it
 * alone is index-backed (fast), the record kinds, examples to start from, and why some queries are
 * slow (free text and unindexed fields are scanned in time slices).
 */
export function SyntaxHelp({
  open,
  onOpenChange,
  catalogue,
  onUse,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  catalogue: FieldCatalogue | undefined;
  onUse: (query: string) => void;
}) {
  const { t } = useTranslation();
  const english = currentLang() === 'en';
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>{t('search.help.title')}</DialogTitle>
          <DialogDescription>{t('search.help.description')}</DialogDescription>
        </DialogHeader>
        <div className="flex max-h-[70vh] flex-col gap-5 overflow-y-auto pr-1 text-13">
          <section aria-labelledby="search-help-rules" className="flex flex-col gap-2">
            <h3 id="search-help-rules" className="text-sm font-semibold text-foreground">
              {t('search.help.rulesTitle')}
            </h3>
            <ul className="flex list-disc flex-col gap-1 pl-5 text-foreground">
              {RULES.map((rule) => (
                <li key={rule}>{t(`search.help.rules.${rule}`)}</li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="search-help-examples" className="flex flex-col gap-2">
            <h3 id="search-help-examples" className="text-sm font-semibold text-foreground">
              {t('search.help.examplesTitle')}
            </h3>
            <ul className="flex flex-col gap-1.5">
              {EXAMPLES.map((example, index) => (
                <li key={example} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <code className="rounded-md bg-subtle px-1.5 py-0.5 font-mono text-xs text-foreground">
                    {example}
                  </code>
                  <span className="text-muted">{t(`search.help.examples.${index}`)}</span>
                  <Button
                    variant="link"
                    size="sm"
                    className="h-auto"
                    aria-label={t('search.help.useExample', { query: example })}
                    onClick={() => {
                      onUse(example);
                    }}
                  >
                    {t('search.help.use')}
                  </Button>
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="search-help-speed" className="flex flex-col gap-2">
            <h3 id="search-help-speed" className="text-sm font-semibold text-foreground">
              {t('search.help.speedTitle')}
            </h3>
            <ul className="flex list-disc flex-col gap-1 pl-5 text-foreground">
              {SPEED.map((item) => (
                <li key={item}>{t(`search.help.speed.${item}`)}</li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="search-help-fields" className="flex flex-col gap-2">
            <h3 id="search-help-fields" className="text-sm font-semibold text-foreground">
              {t('search.help.fieldsTitle')}
            </h3>
            {catalogue ? (
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-13">
                  <caption className="sr-only">{t('search.help.fieldsTitle')}</caption>
                  <thead className="bg-subtle text-left text-xs text-muted-strong">
                    <tr>
                      <th scope="col" className="px-3 py-1.5 font-medium">
                        {t('search.help.columns.field')}
                      </th>
                      <th scope="col" className="px-3 py-1.5 font-medium">
                        {t('search.help.columns.aliases')}
                      </th>
                      <th scope="col" className="px-3 py-1.5 font-medium">
                        {t('search.help.columns.meaning')}
                      </th>
                      <th scope="col" className="px-3 py-1.5 font-medium">
                        {t('search.help.columns.example')}
                      </th>
                      <th scope="col" className="px-3 py-1.5 font-medium">
                        {t('search.help.columns.speed')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {catalogue.fields.map((field) => (
                      <tr key={field.name} className="border-t border-border align-top">
                        <th
                          scope="row"
                          className="px-3 py-1.5 text-left font-mono text-xs font-medium"
                        >
                          {field.name}
                        </th>
                        <td className="px-3 py-1.5 font-mono text-xs text-muted-strong">
                          {field.aliases.join(', ') || '—'}
                        </td>
                        <td className="px-3 py-1.5">
                          <span className="font-medium text-foreground">
                            {english ? field.label_en : field.label_tr}
                          </span>
                          <span className="block text-muted">
                            {english ? field.help_en : field.help_tr}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 font-mono text-xs">
                          {field.examples[0] ?? '—'}
                        </td>
                        <td className="px-3 py-1.5">
                          {field.indexed.length > 0 ? (
                            <Badge tone="success" icon={Zap} title={field.indexed.join(', ')}>
                              {t('search.help.indexedLabel')}
                            </Badge>
                          ) : (
                            <span className="text-muted">{t('search.help.scanned')}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Skeleton className="h-40" />
            )}
          </section>

          {catalogue ? (
            <section aria-labelledby="search-help-kinds" className="flex flex-col gap-2">
              <h3 id="search-help-kinds" className="text-sm font-semibold text-foreground">
                {t('search.help.kindsTitle')}
              </h3>
              <ul className="flex flex-wrap gap-x-4 gap-y-1">
                {catalogue.kinds.map((kind) => (
                  <li key={kind.key}>
                    <code className="font-mono text-xs">{`type:${kind.key}`}</code>
                    <span className="text-muted">{` ${english ? kind.label_en : kind.label_tr}`}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
