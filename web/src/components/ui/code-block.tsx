import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

import { Button } from './button';
import { toast } from './toast';

interface CodeBlockProps {
  code: string;
  /** Small caption above the code, e.g. "FortiGate CLI". */
  label?: string;
  /** Wrap long lines (raw log lines) instead of scrolling horizontally. */
  wrap?: boolean;
  className?: string;
}

/** Monospace block with a copy button: raw log lines, hashes, device commands. */
export function CodeBlock({ code, label, wrap = false, className }: CodeBlockProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      toast({ title: t('common.copied'), variant: 'success' });
      window.setTimeout(() => {
        setCopied(false);
      }, 1500);
    } catch {
      toast({ title: t('common.copyFailed'), variant: 'danger' });
    }
  }

  return (
    <div className={cn('overflow-hidden rounded-lg border border-border bg-subtle', className)}>
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-1">
        <span className="text-xs font-medium text-muted-strong">{label}</span>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('common.copy')}
          title={t('common.copy')}
          onClick={() => void copy()}
        >
          {copied ? (
            <Check aria-hidden="true" className="text-success" />
          ) : (
            <Copy aria-hidden="true" />
          )}
        </Button>
      </div>
      <pre
        className={cn(
          'max-h-80 overflow-auto p-3 font-mono text-xs leading-5 text-foreground',
          wrap ? 'break-all whitespace-pre-wrap' : 'whitespace-pre',
        )}
        tabIndex={0}
      >
        <code>{code}</code>
      </pre>
    </div>
  );
}
