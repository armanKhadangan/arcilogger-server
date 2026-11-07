import { useSystemDatabase } from '@/lib/system-hooks';

import { DatabaseView } from './database-view';

/**
 * Veritabanı (`/database`, `system.read`; Wave 5 addendum C, C7): `GET /system/database`, every
 * 30 s in the background (`system-hooks.ts`), wired to `DatabaseView`; "Yenile" and "Tekrar dene"
 * ask again as the user (`refresh()`).
 */
export function DatabasePage() {
  const database = useSystemDatabase();
  return (
    <DatabaseView
      database={database.data}
      error={database.error}
      loading={database.isPending}
      updatedAt={database.dataUpdatedAt}
      onRefresh={() => {
        void database.refresh();
      }}
    />
  );
}
