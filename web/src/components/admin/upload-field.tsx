import { Upload } from 'lucide-react';
import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useAdminUpload } from '@/lib/api-hooks';
import { formatBytes } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { UploadKind, UploadPreview } from '@/lib/settings-types';

/** Largest file per kind (contract 2.4.4); the API refuses a larger one with 413. */
const LIMITS: Record<UploadKind, number> = {
  kamusm_root: 64 * 1024,
  smtp_ca: 256 * 1024,
  zamane_client: 25 * 1024 * 1024,
};

/**
 * A file the administration service may install (`POST /admin/uploads`): chosen, sent, and
 * inspected by the server, whose answer (`UploadPreview`) the caller shows before anything is
 * installed. The file stays staged for 30 minutes.
 */
export function UploadField({
  kind,
  label,
  hint,
  accept,
  disabled = false,
  onUploaded,
}: {
  kind: UploadKind;
  label: string;
  hint: ReactNode;
  accept: string;
  disabled?: boolean;
  onUploaded: (preview: UploadPreview) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = useId();
  const upload = useAdminUpload();
  const [file, setFile] = useState<File | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const tooLarge = file !== null && file.size > LIMITS[kind];

  async function send() {
    if (!file || tooLarge) return;
    setProblem(null);
    try {
      const preview = await upload.mutateAsync({ kind, file });
      setFile(null);
      setInputKey((value) => value + 1);
      onUploaded(preview);
    } catch (error) {
      setProblem(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-13 font-medium text-foreground">
        {label}
      </label>
      <input
        key={inputKey}
        id={id}
        type="file"
        accept={accept}
        disabled={disabled || upload.isPending}
        aria-describedby={`${id}-hint`}
        aria-invalid={tooLarge ? true : undefined}
        onChange={(event) => {
          setProblem(null);
          setFile(event.target.files?.[0] ?? null);
        }}
        className="block w-full max-w-xl text-13 text-foreground file:mr-3 file:h-8 file:rounded-lg file:border file:border-border file:bg-surface file:px-3 file:text-13 file:font-medium file:text-foreground hover:file:bg-subtle"
      />
      <p id={`${id}-hint`} className={tooLarge ? 'text-xs text-danger-ink' : 'text-xs text-muted'}>
        {tooLarge
          ? t('admin.upload.tooLarge', { size: formatBytes(LIMITS[kind], lang) })
          : file
            ? t('admin.upload.chosen', { name: file.name, size: formatBytes(file.size, lang) })
            : hint}
      </p>
      <div>
        <Button
          variant="secondary"
          disabled={disabled || !file || tooLarge}
          busy={upload.isPending}
          onClick={() => void send()}
        >
          <Upload aria-hidden="true" />
          {upload.isPending ? t('common.working') : t('admin.upload.send')}
        </Button>
      </div>
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
    </div>
  );
}
