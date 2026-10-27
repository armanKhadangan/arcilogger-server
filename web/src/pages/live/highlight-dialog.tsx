import { Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';

import {
  HIGHLIGHT_CLASSES,
  HIGHLIGHT_COLORS,
  MAX_RULES,
  ruleField,
  type HighlightColor,
  type HighlightRule,
} from './live-model';

function Swatch({ color }: { color: HighlightColor }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block size-3.5 rounded-sm border border-border',
        HIGHLIGHT_CLASSES[color],
      )}
    />
  );
}

/**
 * "Vurgulama kuralları": up to 10 rules, a text (its occurrences are marked) or `field:value`
 * (the whole line is marked), each with a colour that keeps the text readable in light and dark.
 * Kept for this tab only (`sessionStorage`): a search term must not stay on a shared PC.
 */
export function HighlightDialog({
  open,
  rules,
  onOpenChange,
  onSave,
}: {
  open: boolean;
  rules: readonly HighlightRule[];
  onOpenChange: (open: boolean) => void;
  onSave: (rules: HighlightRule[]) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<HighlightRule[]>([...rules]);
  const [seen, setSeen] = useState(rules);
  // The dialog opens with the rules in force (a new draft each time they change).
  if (seen !== rules) {
    setSeen(rules);
    setDraft([...rules]);
  }

  function change(index: number, next: Partial<HighlightRule>) {
    setDraft((current) => current.map((rule, at) => (at === index ? { ...rule, ...next } : rule)));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    onSave(
      draft.map((rule) => ({ ...rule, match: rule.match.trim() })).filter((rule) => rule.match),
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setDraft([...rules]);
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-xl">
        <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
          <DialogHeader>
            <DialogTitle>{t('live.highlight.title')}</DialogTitle>
            <DialogDescription>{t('live.highlight.description')}</DialogDescription>
          </DialogHeader>
          {draft.length === 0 ? (
            <p className="text-13 text-muted">{t('live.highlight.empty')}</p>
          ) : (
            <ul className="flex flex-col gap-3" aria-label={t('live.highlight.list')}>
              {draft.map((rule, index) => (
                <li key={index} className="flex flex-wrap items-end gap-2">
                  <div className="flex min-w-48 flex-1 flex-col gap-1">
                    <Label htmlFor={`highlight-${index}`} className="text-xs text-muted-strong">
                      {t('live.highlight.match', { number: index + 1 })}
                    </Label>
                    <Input
                      id={`highlight-${index}`}
                      mono
                      autoComplete="off"
                      spellCheck={false}
                      maxLength={200}
                      value={rule.match}
                      placeholder={t('live.highlight.placeholder')}
                      aria-describedby={`highlight-${index}-kind`}
                      onChange={(event) => {
                        change(index, { match: event.target.value });
                      }}
                    />
                    <span id={`highlight-${index}-kind`} className="text-xs text-muted">
                      {rule.match.trim() === ''
                        ? '\u00a0'
                        : ruleField(rule.match)
                          ? t('live.highlight.kindField')
                          : t('live.highlight.kindText')}
                    </span>
                  </div>
                  <div className="flex flex-col gap-1 pb-5">
                    <Label
                      htmlFor={`highlight-${index}-color`}
                      className="text-xs text-muted-strong"
                    >
                      {t('live.highlight.color')}
                    </Label>
                    <Select
                      value={rule.color}
                      onValueChange={(color) => {
                        change(index, { color: color as HighlightColor });
                      }}
                    >
                      <SelectTrigger id={`highlight-${index}-color`} className="w-36">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {HIGHLIGHT_COLORS.map((color) => (
                          <SelectItem key={color} value={color}>
                            <span className="flex items-center gap-2">
                              <Swatch color={color} />
                              {t(`live.highlight.colors.${color}`)}
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="mb-5"
                    aria-label={t('live.highlight.remove', { number: index + 1 })}
                    onClick={() => {
                      setDraft((current) => current.filter((_, at) => at !== index));
                    }}
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <div>
            <Button
              variant="secondary"
              size="sm"
              disabled={draft.length >= MAX_RULES}
              onClick={() => {
                setDraft((current) => [
                  ...current,
                  {
                    match: '',
                    color: HIGHLIGHT_COLORS[current.length % HIGHLIGHT_COLORS.length] ?? 'yellow',
                  },
                ]);
              }}
            >
              <Plus aria-hidden="true" />
              {t('live.highlight.add')}
            </Button>
            {draft.length >= MAX_RULES ? (
              <p className="mt-1 text-xs text-muted">
                {t('live.highlight.limit', { max: MAX_RULES })}
              </p>
            ) : null}
          </div>
          <p className="text-xs text-muted">{t('live.highlight.storage')}</p>
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => {
                setDraft([...rules]);
                onOpenChange(false);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button type="submit">{t('live.highlight.save')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
