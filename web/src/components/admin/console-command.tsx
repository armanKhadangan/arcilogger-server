import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { CodeBlock } from '@/components/ui/code-block';

/**
 * A server console command with its copy button (`console-commands.ts`); `null`: the value it
 * would be built from is not of the expected form, so the page says so and shows no command.
 */
export function ConsoleCommand({ label, command }: { label: string; command: string | null }) {
  const { t } = useTranslation();
  if (command === null)
    return (
      <Alert tone="danger" data-testid="console-command-refused">
        {t('admin.console.refused')}
      </Alert>
    );
  return <CodeBlock label={label} code={command} wrap />;
}
